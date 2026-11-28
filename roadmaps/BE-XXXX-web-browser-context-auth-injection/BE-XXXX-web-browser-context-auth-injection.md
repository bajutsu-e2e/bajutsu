**English** · [日本語](BE-XXXX-web-browser-context-auth-injection-ja.md)

# BE-XXXX — Inject cookies, storage state, and scoped headers into the web browser context

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-web-browser-context-auth-injection.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md), [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md), [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md), [BE-0130](../BE-0130-default-network-secret-redaction/BE-0130-default-network-secret-redaction.md), [BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md), [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md), [BE-0464](../BE-0464-web-navigate-step/BE-0464-web-navigate-step.md), [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks.md), [BE-0501](../BE-0501-scenario-command-hooks/BE-0501-scenario-command-hooks.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets a web target start a scenario already signed in. A `browser.context` block holds
cookies, a Playwright storage-state file, and extra request headers. The web backend applies the
block to the browser context before the first step runs. A header carries a scope, and the default
scope sends it to the app's own origin alone.

[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) lists seeded
`localStorage` and cookies as launch inputs of the web backend, but no code applies them. Today a
scenario reaches a screen behind a login by driving the login screen through the UI.

## Motivation

A deterministic test cannot always sign in through the UI. A login behind Single Sign-On (SSO), an
external identity provider, multi-factor authentication, or a CAPTCHA cannot run on every build.
Some organizations forbid automated sign-in against production-like systems. Teams in that position
issue a test session token out of band, with a script, and hand it to the browser as a cookie.

Bajutsu gives that token no way in. The `new_context` call of the web driver receives
`reduced_motion` and a device descriptor
([BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md)). Nothing in
the code calls `add_cookies`, `storage_state`, or `extra_http_headers`.

Headers raise a second need. Apps switch behavior on a request header: a feature flag, an A/B
bucket, a tenant, or a check that decides whether to serve the mobile site. A header set on the
whole context would also reach every third-party request, such as map tiles, an analytics tag, or a
fetch from a Web Worker. A custom header on a cross-origin request triggers a Cross-Origin Resource
Sharing (CORS) preflight, which can break the request. It also leaks an internal header to a host
that must never see it. The header needs a scope that defaults to the app's own origin.

Once this item ships, two differences are observable. A scenario with no login step starts on an
authenticated page and passes. The report lists the names of the injected cookies and headers and
none of their values. The file `network.json` marks which exchanges carried an injected header, so
a reader can confirm after the run that no cross-origin request carried one.

## Detailed design

### The `browser.context` block

The block belongs to the `runsOn.browser` group that
[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md) defines for a
web target, next to `engine` and `version`. A scenario writes it under
`preconditions.runsOn.web.browser.context`, so one field has one spelling at both levels.

```yaml
# targets.web.runsOn.browser.context, or preconditions.runsOn.web.browser.context
context:
  cookies:
    - name: session
      value: "${hooks.session.token}"
      domain: app.example.test          # default: the host of app.url
      path: /                           # default: /
      secure: true
      httpOnly: true
      sameSite: Lax
  storageState: "${hooks.session.statePath}"   # a Playwright storage-state JSON file
  headers:
    values: { X-Feature-Variant: mobile }
    scope: sameOrigin                   # sameOrigin (default) | all | ["https://api.example.test"]
```

| Field | Meaning |
|---|---|
| `cookies` | Cookies added with Playwright's `add_cookies`. An entry takes `name`, `value`, `domain`, `path`, `secure`, `httpOnly`, `sameSite`, and `expires`. |
| `storageState` | The path of a Playwright storage-state file with cookies and per-origin `localStorage`. |
| `headers.values` | Header names and values to add to matching requests. |
| `headers.scope` | Which requests get the headers: `sameOrigin`, `all`, or a list of exact origins. |

`cookies` and `storageState` combine. The context starts from the state file, and the listed cookies
are layered on top. A Cookie without `domain` takes the host of the target's `app.url`. A `domain` or origin
that does not parse is a load error that names the field.
A `headers.values` name that the Fetch standard forbids, such as `Cookie` or `Host`, is a load
error, because Playwright drops an override of one silently; a cookie goes in `cookies`.

### Merging the two levels

A target sets the baseline, and a scenario overrides it, because the account changes per scenario.
Cookies merge by the triple of `name`, `domain`, and `path`, and the scenario's entry wins. The
`headers.values` map merges key by key. A scenario that writes `headers.scope` or `storageState`
replaces the target's value whole. This follows the merge rule of `launch.env` in BE-0452.

It also departs from BE-0228, which limits device emulation to the target. Emulation is the same for
every scenario of a target, while credentials are not.

### Values resolved after the hooks run

A value accepts `${hooks.<name>.<key>}`, `${secrets.<name>}`, and `${row.<column>}` tokens.
[BE-0501](../BE-0501-scenario-command-hooks/BE-0501-scenario-command-hooks.md) runs the
`beforeScenario` commands before the device lease and the launch. Their outputs exist before the
browser context does. The runner resolves the block in that window, after the commands and before
`new_context`. A command that issues a session token feeds a cookie directly.

The loader checks each `${hooks.*}` token statically against the visible `beforeRun` and
`beforeScenario` entries, as it does for steps. A token naming no visible entry is a load error. A
`storageState` path resolves against the suite root after token substitution.

The resolved path must sit in one of two places. A path written in the scenario stays inside the
suite root
([BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md)). A
state file that a command generates goes into a per-scenario scratch directory instead. Bajutsu
creates that directory before the scenario's `beforeScenario` commands, passes its path to them as
`BAJUTSU_SCRATCH_DIR`, and deletes it after the scenario's `afterScenario` commands. The scratch
directory lies outside both the suite tree and the run directory, so a live token never lands in a
git checkout or in evidence.

[BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md) expands a `data` row
into its own scenario at load time, and each derived scenario launches its own browser context. A
row gets its Cookie from `${row.token}` with no extra isolation work, and rows never share state.

### Applying the state to the context

The web driver keeps the resolved block next to the device descriptor. It applies the block wherever
it builds a context: the starter's initial context, `_new_context`, `reset_context`, and `relaunch`.
The `storage_state` option goes into the `new_context` arguments, and `add_cookies` runs right after
the context exists. The resolved values stay in driver memory for the run and nowhere else. The
invariant of BE-0228 holds: a fresh context keeps the identity of the context it replaces.

The block sits under the `web` key of a scenario's `runsOn`, so by BE-0452's rule for `runsOn` it has
no effect on a run that drives no web target. A non-web target's own `runsOn` has no `browser`
group, so a `browser.context` written there fails at load and names the field.

### Scoping the headers

The scope decides which requests get the headers.

| Scope | Requests that get the headers |
|---|---|
| `sameOrigin` | Requests whose origin equals the origin of the target's `app.url`. |
| `all` | Every request. |
| A list | Requests to the exact origins listed, each written as scheme, host, and port. |

The implementation is a context-level route handler. It sends a matching request with the headers
added, following redirects itself as the next subsection describes, and calls `route.fallback()`
for any other. It does not use `set_extra_http_headers`,
which has no scope. Routing disables the browser's HTTP cache, so a target that sets `headers` runs
uncached; the manifest records this alongside the scope.

The web network collector already registers a page-level handler when a scenario declares mocks.
That handler ends with `route.continue_()`, and a `continue_()` ends the handler chain. An unmocked
request would never reach the context-level handler. The mock handler switches to
`route.fallback()` for an unmocked request. A mocked request never leaves the browser, so it carries
no injected header, and evidence still records it as mocked.

### Redirects

A plain `route.continue_(headers=…)` cannot hold the scope across a redirect. Playwright applies
the `headers` option to the routed request and to every redirect it starts. A route handler also
sees the first address of a redirect chain alone. A same-origin request that redirects to an
external identity provider would carry every injected header there, and the handler never sees the
hop.

The handler therefore follows redirects itself. For a matching request, it calls
`route.fetch(headers={**request.headers, **{k.lower(): v for k, v in values.items()}}, max_redirects=0)`,
merging the injected headers over the request's own, with the injected names lower-cased so they
replace a header the page already sends, and returns the response with `route.fulfill`. A `3xx`
response goes back to the browser unchanged. The browser follows the `Location` as a new request,
which reaches the handler again and gets its own origin check. A cross-origin hop then falls to
`route.fallback()` and leaves with no injected header.

This design rests on one behavior: a redirect the handler fulfills reaches the route handler again
on every engine. The spike unit measures that behavior on Chromium, Firefox, and WebKit, and it gates
the implementation. If any engine skips the handler for a fulfilled redirect, the item stops and
returns to design before it claims `sameOrigin` on that engine.

The design has a second cost. `route.fetch` runs the request from Playwright's own request context
and reads the whole response before `route.fulfill` returns it, with a default timeout of 30
seconds. A same-origin response that streams then reaches the page at once or not at all: a
Server-Sent Events stream, a long poll, a chunked response, or a large download. The spike measures
these responses too. If they break a page that works without the block, the item returns to design,
as it does for redirects.

### Evidence and masking

The manifest records the injected cookie names, the injected header names, and the scope. It
records no value. Each `network.json` exchange gains an `injectedHeaders` list with the names of the
headers the handler added to that request. A missing or empty list means none, so older evidence
stays valid. A reader checks the scope by looking for an exchange with a cross-origin address and a
non-empty list.

Three rules keep the values out of evidence.

- The injected header names join the run's masked-header set
  ([BE-0130](../BE-0130-default-network-secret-redaction/BE-0130-default-network-secret-redaction.md)).
  Their values stay masked in `network.json` by default.
- The existing defaults for the `cookie` and `set-cookie` headers mask an injected Cookie value in
  each exchange.
- The run never copies a storage-state file into the run directory, because the file holds live
  tokens.

### Prime directives

The block holds plain data that the runner applies before the first step. Nothing in it waits, branches, or calls a
Large Language Model (LLM). The run stays deterministic, and the assertions keep the verdict. The
values belong to the app under test and live in config and scenario files, so the tool stays
app-agnostic.

### What the design leaves out

The block does not save a browser's state back into a file. A later item can add a `saveState` step
for that. The block also does not write `document.cookie` or run script in the page.
While `headers` is set, a matching same-origin response does not stream to the page, as *Redirects*
describes.

## Alternatives considered

**Sign in through the UI.** This cannot work behind SSO, multi-factor authentication, or a CAPTCHA.
Where it works, it adds time and a point of breakage to every scenario, in a part of the flow the
scenario does not test.

**Sign in with an `http` step and pass the Cookie on.** The `http` step stores the values it reads
in variables, but no path moves them into the browser context. Building that path is this item's
feature under another name.

**Write `document.cookie` from a script step.** No script step exists. Page script also cannot set
an `httpOnly` cookie, which is the form most session cookies take.

**Accept a `storageState` file alone.** A command writes the file, and the config points at it. This
is the smallest change, and this item keeps it as one field. It carries no headers and gives no
per-scenario override, so both motivating cases stay open.

**Set headers with `extra_http_headers` on the context.** The call is one line, but it has no scope.
It sends every header to every origin, which is the leak the Motivation describes.

**Default the scope to `all`.** This matches what most test tools do. A wrong default here sends a
secret outside the app's origin. A `sameOrigin` default costs an author who wants `all` one extra
line, so the default favors the safe side.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Spike (go/no-go gate): confirm that a fulfilled redirect re-enters the route handler and that streaming and long-lived same-origin responses still work, and measure Web Workers and service workers, on Chromium, Firefox, and WebKit
- [ ] Schema, merge rules, and static token checks for `browser.context`
- [ ] Context wiring: the per-scenario scratch directory, `storage_state`, `add_cookies`, and re-application on `reset_context` and `relaunch`
- [ ] Scoped header handler with manual redirect following, and the mock handler's switch to `fallback()`
- [ ] Evidence: manifest names, `injectedHeaders`, and default masking
- [ ] Tests, a real-backend scenario with no login step, and the docs in both languages

## References

- `bajutsu/common/drivers/playwright/playwright_driver.py` (`_new_context`)
- `bajutsu/common/drivers/web_network.py` (`WebNetworkCollector._on_route`)
- [Playwright `BrowserContext` reference](https://playwright.dev/python/docs/api/class-browsercontext)
