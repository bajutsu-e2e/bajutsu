**English** · [日本語](BE-XXXX-web-navigate-step-ja.md)

# BE-XXXX — A `navigate` step that opens a URL mid-scenario on the web backend

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-web-navigate-step.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | — |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

This item adds a `navigate` step that loads a URL in the middle of a scenario on the web (Playwright) backend. The step takes a single string, such as `- navigate: /settings`. A path that starts with `/` resolves against the target's `baseUrl`, and an absolute `http` or `https` URL is used as written. The step waits for the page's `load` event and then returns. It does not inspect the HTTP status, so the scenario asserts what the page shows afterward. The step rides on a new `Driver.goto(url)` method and a new `urlNavigation` capability. Only the web backend advertises the capability, so a scenario that uses `navigate` on iOS or Android fails in preflight, before any device is leased. That includes a `navigate` nested inside an iOS `web:` block.

## Motivation

Today a web scenario can reach a page in only two ways. The browser opens `baseUrl` when a run starts or resets, and the author then taps through the UI until the wanted page appears. The `back` step adds history navigation, but no step opens an arbitrary URL.

That gap causes three problems for the author of a web scenario:

- **Deep pages cost a UI walk.** A scenario that tests an account settings page must first replay the sign-in and menu taps that reach it. Each tap is another place for the scenario to fail for a reason unrelated to the page under test.
- **Entry points that are not links cannot be tested.** A password-reset link from an email, a shared-item URL, or a page reached only by typing the address has no control to tap. The scenario cannot start from that entry point.
- **Redirects cannot be exercised.** An old URL that should redirect, or a protected URL that should send an anonymous visitor to sign-in, needs a step that loads exactly that URL.

The observable outcome is this. A scenario that now spends five to ten steps tapping to a deep page can open the page with one `navigate` step, and a scenario for a redirect or an email link can be written at all. The web demo scenarios will include one that opens a page by URL and asserts its content.

## Detailed design

### Authoring surface

```yaml
steps:
  - navigate: /settings                       # resolves against the target's baseUrl
  - navigate: https://idp.example.com/login   # an absolute URL is used as written
  - navigate: "/items/${vars.itemId}"         # ${...} tokens are expanded like any step field
  - assert: [{ exists: { id: settings.title } }]
```

`navigate` is an ordinary action: a step carries exactly one action, and the common modifiers (`name`, `capture`, `target`) apply as they do to every other action. The YAML value is a plain string. A `Navigate` model with one `url` field accepts that string through a `before` validator that maps it to `{url: <string>}`. `Exists` uses the same validator technique to accept its inline form (`bajutsu/common/scenario/models/assertions/exists.py:21`). The validator rejects a mapping, so `{url: ...}` does not become an undocumented public shape that a later `waitUntil` option must stay compatible with.

URL resolution follows the standard reference resolution (`urllib.parse.urljoin`), with the target's `baseUrl` as the base:

| Value | Result |
|---|---|
| `/settings` | the `baseUrl` origin plus `/settings`; any path in `baseUrl` is replaced |
| `settings` | resolved against the `baseUrl` path, as a browser resolves a relative link |
| `https://other.example/x` | used as written; a different origin is allowed |
| any scheme other than `http` or `https` | rejected when the scenario loads |

Rejecting other schemes keeps `file:`, `data:`, and `javascript:` out of the step. The load-time check applies only to values with no `${...}` token. A value that contains a token is checked again after expansion, at run time. One shared function holds the scheme rule, and both checks call it. `HttpRequest` shares `first_duplicate_var` between its load-time and run-time checks the same way (`bajutsu/common/scenario/models/actions/http_request.py:20`). Resolution offers no protection of its own, because `urljoin` returns an absolute reference such as `javascript:alert(1)` unchanged.

### Behavior at run time

1. The handler expands `${...}` tokens in the string and applies the scheme rule to the result. It then passes the possibly relative reference to `Driver.goto`. The driver, not the handler, resolves the reference against `baseUrl`. The Playwright driver already holds the base (`playwright_driver.py:57`), so the orchestrator gains no new dependency on target config. The contract of `Driver.goto` is therefore "a reference, resolved against the backend's own base".
2. The Playwright driver records an actuation (`gesture: navigate`), calls `page.goto(url)`, and returns once the `load` event fires. The step adds a history entry. Cookies and storage persist, so `navigate` is not a context reset (`reset_context` stays the crawl's clean-start primitive).
3. The step does not read the response status. A 404 page loads, the step passes, and the next `assert` or `wait` decides the verdict. `last_nav_status` stays a crawl-only signal (`bajutsu/common/drivers/playwright/_functions.py:143`).
4. A navigation error that the URL itself causes, such as a failed name lookup, a refused connection, or a timeout, fails the step with Playwright's message. The driver must not classify it as a browser wedge. `_wedge_guard` converts any Playwright error into a recoverable `DeviceError` (`bajutsu/common/drivers/playwright/_functions.py:109`), which the crawl's workers answer by relaunching the browser. A scenario run would then treat an author's typo as a browser fault. `goto` therefore needs its own error mapping.

The step stays inside the prime directives. It contains no fixed pause, the wait is the browser's own `load` condition, and the verdict still comes only from assertions.

### Driver, capability, and preflight

| Layer | Change | File |
|---|---|---|
| Scenario model | `Navigate` action; `Step.navigate` field | `bajutsu/common/scenario/models/actions/navigate.py` (new), `steps/step.py` |
| Driver protocol | `def goto(self, url: str) -> None` | `bajutsu/common/drivers/base/driver.py` |
| Capability | `URL_NAVIGATION = "urlNavigation"` | `bajutsu/common/drivers/base/capability.py` |
| Preflight | a `_Requirement` for steps with `navigate`, including steps nested in a `web:` block | `bajutsu/common/capability/capability_preflight.py` |
| Handler | `@_handler("navigate")` next to `back` | `bajutsu/common/orchestrator/actions/handlers/navigation.py` |
| Web driver | `goto`, with its own error mapping | `bajutsu/common/drivers/playwright/playwright_driver.py` |
| Fake driver | records the call and advertises the capability | `bajutsu/common/drivers/fake/fake_driver.py` |
| Other drivers | raise `UnsupportedAction`, the way `select_option` does on iOS and Android | `adb`, `xcuitest`, `xcuitest_live`, `webview` drivers |
| Runner sets | add `navigate` to the screen-action and no-selector sets | `bajutsu/common/orchestrator/loop/_step_runner.py:125` and `:149` |
| Tracing and actuation | add `goto` to `_TRACED_METHODS`; add the `navigate` gesture and a `url` channel | `drivers/tracing.py`, `drivers/actuation/_shared.py` |

The driver method is named `goto` because `BackendLifecycle.navigate()` already means "open `baseUrl`" and takes no argument (`bajutsu/common/drivers/base/backend_lifecycle.py:25`). A second `navigate` with a different meaning on one driver would confuse readers.

`navigate` resolves no selector, so on a device group it would land on whichever member is in front. It joins `back` in the no-selector set for that reason (BE-0447).

### Codegen, authoring, and documentation surfaces

- The Playwright generator emits `await page.goto(new URL(<value>, BASE_URL).toString());`, which matches the driver's resolution. The generator renders the value through its own `_ts()` string-literal helper (`bajutsu/codegen/playwright.py:74`), as it renders every other literal, because a `'` is legal in a URL query. Values it cannot render as a literal fall back to the existing labeled `// TODO` form (`bajutsu/codegen/playwright.py`, near the `back` case at line 376). The XCUITest and UI Automator generators emit the same labeled `TODO`.
- The step vocabulary is listed in several places beyond the model. The implementation checks and updates `bajutsu/common/scenario/edit.py`, `bajutsu/repl/session.py`, and `app_crash_latches.py`, then adds the action to `docs/scenarios.md`, `docs/dsl-grammar.md`, `docs/drivers.md`, `docs/codegen.md`, and their `docs/ja/` mirrors.

### Out of scope

- iOS deep links and Android intents. The `Driver.goto` name and the capability token leave room for them, but each needs its own item (URL scheme resolution, the confirmation dialog iOS shows, and the `DeviceControl` surface).
- A host allowlist. The `http` step also accepts any URL, so `navigate` adds no new reach (`bajutsu/common/scenario/models/actions/http_request.py`). A hosted-run policy, if wanted, belongs under the security items and covers both steps.
- Response-status checking and a `waitUntil` option. The string form leaves room to grow into a mapping later.
- Replacing `reset_context` or the `baseUrl` launch.

### Work breakdown (MECE)

| # | Unit | Done when |
|---|---|---|
| 1 | Scenario model: `Navigate`, `Step.navigate`, string shorthand, scheme check | model tests load the string shorthand, and reject a mapping, `javascript:`, and an empty value |
| 2 | Driver protocol, capability token, preflight requirement, non-web drivers raise | preflight tests show iOS and Android targets fail before device lease with a message naming `navigate`. A `navigate` nested in a `web:` block on an iOS target fails there too, because the preflight's deliberate refusal to recurse into `web.steps` (`capability_preflight.py:95`) must not let this token reach a leased device |
| 3 | Handler, token expansion, runner sets, tracing, actuation record | a fake-driver run executes `navigate`, expands `${vars.*}`, and records the actuation. A value that expands to `javascript:` or `file:` fails the step through the same scheme rule unit 1 applies at load time |
| 4 | Playwright `goto`: URL resolution, `load` wait, error mapping | web driver tests cover relative and absolute values, a failed lookup that fails the step without triggering recovery, and a 404 that passes |
| 5 | Driver conformance case | the web conformance suite passes with a new `goto` case, and non-web backends skip it by capability |
| 6 | Codegen | the Playwright output for a `navigate` scenario matches a golden, and the other generators emit the labeled `TODO` |
| 7 | Web demo scenario | a scenario under the web demo opens a page by URL and asserts its content, and it passes in the web E2E lane |
| 8 | Documentation and vocabulary lists | the English and Japanese pages list `navigate`; `make check` is green |

### Open points for review

- **Relative resolution.** We chose standard `urljoin`, so `/settings` drops any path in `baseUrl`. A target whose `baseUrl` is `https://host/app/` would then need `app/settings` or `/app/settings`. Appending to the `baseUrl` path is the alternative.
- **Error classification.** Unit 4 must separate a bad URL from a browser fault. The exact mapping of Playwright's `net::ERR_*` errors needs a test against a real browser.
- **Scheme restriction.** The `http`/`https` rule goes beyond what `http` enforces. We keep it because a `file:` URL would read the worker's disk on a hosted run.

## Alternatives considered

| Alternative | Why we did not choose it |
|---|---|
| A Playwright-only action that skips the Driver protocol | It breaks the app-agnostic directive, and a later iOS or Android version would have to redefine the step. The capability gate costs little and keeps the step's meaning backend-neutral. |
| Absolute URLs only | Authors would rewrite every scenario per environment, and `baseUrl` would stop being the one place that names the environment. |
| A mapping form (`navigate: { url: ... }`) from the start | The step has one argument today, so the mapping adds noise to every use. Accepting a string first leaves a mapping open for `waitUntil` later. |
| Restrict navigation to the `baseUrl` origin by default | It blocks external sign-in providers and payment pages that real scenarios cross. It also makes `navigate` stricter than `http`, which has no host limit. A hosted-run policy would cover both. |
| Fail the step on a 4xx or 5xx response | It is the safer default, but we chose a plain `load` step. Scenarios that test an error page would then need an opt-out. Revisit it if silent 404s prove common. |
| Extend `relaunch` with a URL | `relaunch` restarts the app process. The web equivalent is a context reset, which drops cookies and storage, so it cannot express "continue the session on another page". |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Scenario model: `Navigate` and `Step.navigate`
- [ ] Driver protocol, capability, preflight
- [ ] Handler, runner sets, tracing, actuation record
- [ ] Playwright `goto` with URL resolution and error mapping
- [ ] Driver conformance case
- [ ] Codegen
- [ ] Web demo scenario
- [ ] Documentation and vocabulary lists

Log:

- Proposed.

## References

- [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) — added `back` as a cross-backend step, the precedent for this item's driver and handler path.
- [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server.md) — brings up the server behind a web target's `baseUrl`.
- `docs/scenarios.md`, section `selectOption` — the precedent for a web-only step gated by a capability.
