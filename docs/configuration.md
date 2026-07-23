**English** · [日本語](ja/configuration.md)

# Configuration, onboarding a target, and doctor

The tool core is app-agnostic. All app-specific differences belong in config, allowing multiple apps to run with the same binary and the same drivers. Adding a target means adding one `targets.<name>` entry.

Implementation: `bajutsu/config/resolve.py` (resolution) · `bajutsu/doctor.py` (convention score). No config ships in the repo root; pass one with `--config` (default filename `bajutsu.config.yaml`) — the demos ship ready-to-run configs, e.g. [`demos/showcase/showcase.config.yaml`](../demos/showcase/showcase.config.yaml) (iOS) and [`demos/web/demo.config.yaml`](../demos/web/demo.config.yaml) (web).

Related: [app-agnostic in concepts](concepts.md#6-app-agnostic-push-differences-into-config) · [drivers](drivers.md) · [scenarios](scenarios.md)

---

## Config layering (defaults × targets)

`bajutsu.config.yaml` has two layers. The resolution order is **defaults < target < scenario** (the
one closer to the test wins).

```yaml
defaults:                       # shared across all targets
  platform: ios                 # team-wide default platform (ios/android/web); omit to derive it from each target's backend
  backend: [ios]                # ordered list of platforms (ios/android/web/fake) or actuators (xcuitest); a single string is also OK
  device:  "iPhone 15"
  locale:  en_US
  capture: [screenshot.after, elements, actionLog]
  redact:  { headers: [Authorization, Cookie], fields: [token, password] }
  secrets: [LOGIN_PASSWORD]         # env var names usable as ${secrets.X} (values masked in evidence)
  ai:      { provider: api-key, keyEnv: ANTHROPIC_API_KEY }   # the AI paths' provider/model/endpoint/key (below)
  reservedNamespaces: [auth, nav]   # the id contract for shared flows / components (informational)

targets:
  showcase-swiftui:             # ← selected by --target showcase-swiftui
    bundleId:       com.bajutsu.showcase.ios.swiftui     # iOS target (required unless baseUrl is set for web)
    deeplinkScheme: showcaseswiftui
    idNamespaces:   [stable, horse, search, log, notice, perm, sys, net]
    launchEnv:      { SHOWCASE_UITEST: "1" }
    scenarios:      demos/showcase/scenarios   # this target's scenarios dir (run reads it; record writes here)
    dismissAlerts:  { instruction: Allow }     # app default for the alert guard (below); --dismiss-alerts overrides per run
    # optional: erase / network / backend / device / locale / launchArgs / setup / redact / secrets / mockServer / appPath / build

  web:                          # a web target (Playwright backend) is identified by URL
    platform:  web                                  # optional: usually derived from backend/baseUrl, but explicit is clearest
    baseUrl:   "http://127.0.0.1:8787/index.html"   # required for web (instead of bundleId)
    backend:   [web]
    headless:  true                                 # web only: false = a visible (headed) browser; --headed overrides per run
    browser:   chromium                             # web only: rendering engine — chromium / firefox / webkit; --browser overrides per run
    deviceMode: desktop                             # web only: "desktop" (default) or a Playwright device preset (e.g. "iPhone 13") to drive the target as a mobile device
    scenarios: demos/web/scenarios
```

Each platform identifies its target by its own handle: **iOS** by `bundleId`, **web** by `baseUrl`,
**Android** by `package`. A target's `platform` selects which handle is required; it is **optional** and
defaults to the platform its `backend` implies (so a config written before this field is unchanged) —
set it explicitly to be unambiguous. A target carrying the wrong handle for its platform (or none at
all) is rejected at load. See [drivers → Playwright](drivers.md#playwright-web) and `demos/web`.

### Resolution (`resolve` → `Effective`)

`resolve(config, target)` builds the effective values `Effective` (a frozen dataclass) for one target.
An undefined target raises `KeyError` (the CLI exits with code 2).

| `Effective` field | Source | Notes |
|---|---|---|
| `platform` | app < defaults < derived | the target's platform (`ios`/`android`/`web`): explicit `platform` wins, else the target's `backend` implies it, else the identifier present, else `ios`. Selects which identifier is required ([BE-0009](../roadmaps/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)) |
| `bundle_id` | app | iOS target identifier; required when the platform is `ios` |
| `base_url` | app | web target URL (Playwright backend); required when the platform is `web` |
| `package` | app | Android target identifier; required when the platform is `android` |
| `headless` | app | web backend only: `true` (default) runs headless; `false` shows a visible (headed) browser, in slow-motion. `bajutsu run --headed / --no-headed` and the Web UI's "show browser" toggle override per run; iOS ignores it |
| `browser` | app | web backend only: the Playwright rendering engine to drive — `chromium` (default), `firefox`, or `webkit`. All three run headless on Linux. `bajutsu run/record --browser <engine>` overrides per run (flag > config > default), and `bajutsu run --browsers <list>` runs the cross-browser matrix (below); a missing engine binary is installed on demand. An unknown value is rejected at config load. iOS ignores it ([BE-0076](../roadmaps/BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md)) |
| `device_mode` | app | web backend only: the device mode a browser context is created with — `deviceMode: desktop` (the default, unchanged from today) or a Playwright device preset name (e.g. `iPhone 13`) that emulates its viewport / touch / device scale / user agent, driving the web target as that mobile device. It is desktop-browser emulation (Chrome DevTools' device toolbar), not a real device ([drivers → Playwright](drivers.md#playwright-web)). Resolved **lazily** against `playwright.devices` in the driver, so config load never imports Playwright; an unknown preset fails loudly at driver start, not at config load. Distinct from the top-level `device` (the iOS Simulator name), which a web target ignores. iOS / Android ignore this ([BE-0228](../roadmaps/BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation.md)) |
| `device_provider` | app | where this target's devices come from — `deviceProvider: { kind: local }` (the default, today's locally-attached `--udid` path) or another `kind` that a device-cloud adapter registers to reserve a device off-host and hand the run its serial / endpoint. The `kind` is resolved against the device-provider registry **at run time, not config load**, so the deterministic core never imports a cloud SDK; an unknown `kind` fails loudly when the run resolves the provider. Only `bajutsu run` resolves it today — `record`, `crawl`, and `audit --repeat` still resolve devices the old way and silently ignore this field. The seam sits upstream of the device pool and entirely off the run/CI verdict path (a provider only acquires and releases a device). Two built-in providers ship today: **`local`** (the default — the locally-attached `--udid` path, no extra fields) and **`appium`** (the live path to a reserved iOS device behind a self-hosted Appium / WebDriver grid — requires `endpoint: <url>`; Bajutsu drives that endpoint end to end over a live W3C WebDriver transport, resolving selectors Python-side the same way the local XCUITest backend does; see [iOS device cloud](ios-device-cloud.md#live--an-appium-endpoint-provider)). Concrete cloud adapters ship as separate optional packages ([BE-0236](../roadmaps/BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md), [BE-0238](../roadmaps/BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md)) |
| `launch_server` | app | optional `launchServer: {cmd, readyUrl, readyTimeout, cwd, env}` — bring up `baseUrl`'s host for the run, then tear it down: probe `readyUrl` (default `baseUrl`), reuse it if already serving, else run `cmd` and wait until ready (a condition wait, never a fixed sleep). The web analogue of `build` ([BE-0059](../roadmaps/BE-0059-launch-target-server/BE-0059-launch-target-server.md)). For an **uploaded** bundle in `serve`, the host never runs `cmd` directly — `serve --upload-exec` governs it (see [self-hosting](self-hosting.md#uploaded-config-command-execution-be-0090)); a `sandbox` run needs the extra fields `dockerImage` (a Docker image reference, e.g. `node:20-slim`) **or** `dockerfile` (a bundle-relative path built with `docker build`) — exactly one — plus `port` (the in-container listen port, published to a loopback host port) ([BE-0090](../roadmaps/BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md)) |
| `run_defaults.dismiss_alerts` / `.erase` / `.network` | app | per-app defaults for run-behavior settings otherwise set per scenario or on a CLI flag ([BE-0177](../roadmaps/BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)). `dismissAlerts` takes the scenario form (`false`, or `{ enabled, instruction }`) and defaults the alert guard; `erase` defaults `preconditions.erase`; `network` defaults collecting the app's network exchanges. Each resolves **flag > scenario > this > built-in** (guard on, erase off, network on), mirroring `--headed`/`headless`: `bajutsu run --dismiss-alerts/--no-dismiss-alerts`, `--erase/--no-erase`, `--network/--no-network` (and `--alert-instruction`) still override for one run |
| `deeplink_scheme` | app | the scheme used by the preconditions' deeplink |
| `backend` | app ?? defaults | stability-ordered list of platforms (`ios`/`android`/`web`/`fake`) or actuators (`xcuitest`); a single string is listified ([drivers](drivers.md#backend-selection-and-the-actuator)) |
| `device` / `locale` | app ?? defaults | `locale` is applied at launch (`simctl` launch args) |
| `launch_env` / `launch_args` | app | merged/appended by preconditions at run time |
| `ready_when` | app | optional `readyWhen: { id: … }` — a selector the launch waits for before the run starts, instead of the default "the app rendered any 2+ elements". Use it for an app whose first interactive screen is a modal over always-present chrome (the element-count heuristic can return before the modal presents). Its `id` / `idMatches` accept an OR candidate list like a scenario selector (`readyWhen: { id: [stable.row.1, stable_row_1] }`, BE-0221), so one `readyWhen` covers a target whose native id syntax differs. A condition wait, never a fixed sleep. Set it only when **every** scenario for the target starts on that same screen; when first screens vary per scenario, lead each scenario with a `wait` step instead. `readyWhen` stays the strongest readiness signal: on iOS, a target linking `BajutsuKit`'s screen-transition observer (BE-0310) gets a reported-screen-change rung above the namespace/count heuristics for free, but an explicit `readyWhen` still outranks it — an earlier base-screen transition never preempts the modal `readyWhen` waits for. The observer signal governs only when no `readyWhen` is set |
| `id_namespaces` | app | referenced by doctor |
| `reserved_namespaces` | defaults | informational (doctor scores against the app's `idNamespaces` only) |
| `mock_server` | app | ⚠️ schema only · not wired |
| `setup` | app | default reusable prelude (a scenario whose steps run before each scenario's own) |
| `evidence_dirs.scenarios` | app | this app's scenarios dir — `run --target` loads every `*.yaml` here; `record` writes new ones here. Relative to the config file's own directory (like `appPath` and the sibling `evidence_dirs.baselines` / `.schemas` / `.goldens`), so the config behaves the same wherever `bajutsu` runs from (BE-0242). `run --scenario` / `record --out` override it |
| `capture` | defaults | the default evidence ([the note in evidence](evidence.md#three-ways-to-request-evidence)) |
| `redact` | defaults ∪ app | merged (below) |
| `secrets` | defaults ∪ app | env var names declaring `${secrets.X}`; values are masked in evidence ([evidence](evidence.md#masking-redact)) |
| `requires` | defaults ∪ app | capability tokens a worker must advertise to run this target on the hosted backend ([self-hosting](self-hosting.md#capability-routed-queues-be-0166), [BE-0166](../roadmaps/BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md)), e.g. `[ios18, ipad]`. The platform axis is added automatically; add tokens here only to pin a runtime or device class. Ignored by a local single-worker run |
| `ai` | defaults < app (field by field) | the AI paths' provider/model/endpoint/key ([below](#ai-provider-ai-be-0047)); `None` (omitted) = the environment alone decides |
| `defaults.doctor.idCoverageOk` / `defaults.doctor.idCoverageFail` | defaults | id-coverage thresholds for doctor grading ([below](#configurable-thresholds-defaultsdoctor-be-0024)); default 0.9 / 0.7 |

The `backend` field validator `_norm` normalizes "a single string → a one-element list" (on both
defaults / app).

### Merging redact

Config's `defaults.redact` and `targets.<name>.redact` are **union**ed (`_merge_redact`, unioning
`labels`/`headers`/`fields` individually). The scenario's `redact`
([evidence](evidence.md#masking-redact)) layers on top.

### Secrets (`secrets:`)

`secrets:` (a list of **environment-variable names**, declared in `defaults` and/or `targets.<name>`,
unioned by `resolve`) is the declaration site for the `${secrets.X}` variables a scenario can input.
At run time `bajutsu run` resolves each declared name from the environment, interpolates its value
into the action (`${secrets.X}`), and **masks the literal value everywhere it would appear in
evidence** ([evidence](evidence.md#masking-redact)). The scenario source keeps the `${secrets.X}`
token, never the value.

### AI provider (`ai:`, BE-0047)

The AI paths — `record`, `triage --ai`, and the `--dismiss-alerts` guard — reach the model through
one provider configured by an optional `ai` block, declared in `defaults` and/or `targets.<name>`
and merged **field by field** (the target's value wins per field). The block resolves into
`Effective.ai`, so the CLI and `serve` agree on one source of truth. This is the enforcement behind
"your AI, your key, your data": every AI path runs under the key and endpoint you configure, and the
deterministic `run` gate still calls no model at all
([BE-0047](../roadmaps/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)).

```yaml
defaults:
  ai:
    provider: api-key                        # a registered provider name; api-key (default), bedrock, ant, or claude-code ship today
    model:    claude-opus-4-8                 # optional: override the path's default model (or the BAJUTSU_AI_MODEL env)
    effort:   high                            # optional: reasoning effort — low/medium/high/xhigh/max (or BAJUTSU_AI_EFFORT); claude-code
    language: auto                            # optional: AI output language for the generated prose — ja/en/auto (or BAJUTSU_AI_LANGUAGE)
    baseUrl:  https://ai-gateway.internal/v1  # optional: a self-hosted gateway / enterprise proxy (anthropic provider)
    keyEnv:   ANTHROPIC_API_KEY               # the NAME of the env var holding the key — never the key itself
```

- **Model and effort are config-first with an env fallback.** `model` (or `BAJUTSU_AI_MODEL`)
  overrides the default model on any provider; `effort` (or `BAJUTSU_AI_EFFORT`) sets the reasoning
  effort — one of `low`/`medium`/`high`/`xhigh`/`max`, honored by the `claude-code` provider
  (passed to the CLI as `--effort`). An unrecognized `effort` from config or the env var falls back
  to the model's default; the `serve` **Settings** panel instead validates its input and rejects an
  unknown value (HTTP 400) rather than falling back. The panel exposes both; on **local** `serve` the
  saved provider, model, and effort now persist to a serve-owned file and are restored on the next
  start ([BE-0184](../roadmaps/BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md)),
  so a restart no longer resets them to the launch environment; a config `ai:` block still wins over
  a restored value. On a **hosted, multi-tenant** `serve` the selection resolves and persists **per
  organization** ([BE-0229](../roadmaps/BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution.md)),
  so each org's `record` / triage / draft paths use that org's own saved choice; the selection reaches
  a spawned job as a per-job environment overlay rather than the shared process environment, so one
  org's save never changes another org's AI runs. `record` prints the resolved choice up front
  (`🤖 AI: <provider> · model <model> · effort <effort>`).

- **Output language is a separate, config-first knob**
  ([BE-0188](../roadmaps/BE-0188-configurable-ai-output-language/BE-0188-configurable-ai-output-language.md)).
  `language` (or `BAJUTSU_AI_LANGUAGE`) fixes the language the AI writes its *own generated prose* in:
  `record`'s `from:` provenance and `crawl`'s streamed reasoning. It is one of `ja` / `en` / `auto`,
  and `auto` (the default) keeps today's behavior — `record` follows the goal's language and `crawl`
  stays English. Set it per invocation with `--language` on `record` / `crawl` (flag > config >
  `auto`), or from the `serve` **Settings** panel's *Output language* dropdown; an unrecognized value
  falls back to `auto` (the panel instead rejects it with HTTP 400). This governs authoring and
  investigation prose only — never the deterministic `run` verdict — and is distinct from a target's
  device `locale` below, which sets the app/UI language rather than the AI's.

- **A provider is a backend behind one interface**
  ([BE-0104](../roadmaps/BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)).
  The AI paths reach a model only through a vendor-neutral seam (`bajutsu/ai`), mirroring how a
  platform is a backend behind the `Driver` interface. `provider` is therefore an **open,
  registry-validated** value, not a fixed set: `api-key`, `bedrock`, `ant`, and `claude-code` are the
  adapters that ship today. The first three share one Anthropic adapter — the name states the *auth
  method*: a direct API key, AWS credentials for Bedrock, or the `ant` CLI's OAuth token, BE-0163;
  the legacy name `anthropic` still resolves to `api-key`. `claude-code`
  ([BE-0176](../roadmaps/BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md)) is a
  separate adapter that shells out to the local `claude` CLI. An unknown name fails closed with a
  clear error the first time an AI path resolves the provider. (The check lives
  in the AI layer, not at config load: the deterministic core must not import the AI provider stack
  ([BE-0112](../roadmaps/BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement.md)),
  so config accepts the name and the registry that owns the valid names rejects an unregistered one.)
  Adding a model family (e.g. an OpenAI-compatible endpoint) is *registering an adapter*, and it
  inherits the redaction and fail-closed guarantees below by construction.
- **Keys never live in config.** `keyEnv` names an environment variable; the value is read from the
  environment at call time, so a secret never lands in the repo or an uploaded bundle. `baseUrl`
  points the Anthropic SDK at a self-hosted gateway / proxy (`Anthropic(base_url=…,
  api_key=os.environ[keyEnv])`), so your screenshots and element trees only ever reach the endpoint
  you set, never a vendor default. Bedrock keeps the standard AWS credential chain (`AWS_REGION` +
  env / shared profile / instance or task role) and needs a provider-prefixed `model`.
- **`ant` bills a subscription/SSO seat, no API key (BE-0163).** The `ant` provider reaches the model
  through the official [Anthropic CLI](https://github.com/anthropics/anthropic-cli): install it and
  run `ant auth login` (a browser-based OAuth/SSO sign-in against the Claude Console). Bajutsu reads a
  bearer token from the CLI at call time and passes it to the SDK as `auth_token` (rather than an API
  key), so a Claude Pro/Max/Console seat is billed. `ANTHROPIC_PROFILE` selects a named CLI profile;
  no API key is needed, and every AI path (authoring, the alert guard, `triage --ai`) keeps full
  vision. `ant` is an external binary you install yourself — Bajutsu neither vendors nor installs it.
- **`claude-code` bills a Claude Code subscription via the local CLI (BE-0176).** The `claude-code`
  provider shells out to the [`claude` CLI](https://github.com/anthropics/claude-code) (`claude -p`,
  print mode) instead of the Anthropic SDK, so authoring / investigation draw on the Claude Code
  Pro / Max / Console seat you already have signed in (`claude setup-token`, or an interactive
  login). Every AI path keeps full vision: each screenshot is written to a per-call scratch file
  whose path the prompt names, and the CLI is allowed only `Read` (scoped to that directory) to view
  it — every other tool is denied and permission prompts fail closed, since on-screen text is
  untrusted input
  ([BE-0125](../roadmaps/BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md)).
  Any `ANTHROPIC_API_KEY` is stripped from the CLI call so billing stays on the subscription rather
  than the API. `claude` is an external binary you install yourself — Bajutsu neither vendors nor
  installs it. On a **headless host** — a CI runner, a container, a remote `serve` — that can't run
  `claude setup-token`'s interactive browser flow, mint the long-lived token once on a machine that
  can and set `CLAUDE_CODE_OAUTH_TOKEN` (in your shell, a `.env`, or `serve`'s Settings panel, which
  holds it write-once alongside the API key)
  ([BE-0215](../roadmaps/BE-0215-claude-code-oauth-token-credential/BE-0215-claude-code-oauth-token-credential.md));
  the `claude` CLI reads it from the environment, so no interactive login is needed there.
- **Config first, environment fallback.** Any field you omit falls back to today's environment
  variables — `BAJUTSU_AI_PROVIDER`, `ANTHROPIC_API_KEY`, `BAJUTSU_BEDROCK_MODEL` (the `ant` provider
  reads its credential from the CLI, honoring `ANTHROPIC_PROFILE`) — so a config with no `ai` block
  behaves exactly as before.
- **Fail closed.** `record`, `triage --ai`, and an explicitly-requested `--dismiss-alerts` exit with
  a clear, provider-specific error when the selected provider has no usable credential — they never
  construct a client that quietly falls back to a hosted default.
- **The textual inputs are redacted; screenshots cannot be.** The element trees, failure text, and
  the (possibly user-supplied) alert instruction sent to the model are scrubbed by the same run-scoped
  redaction as written evidence (the target's `redact` keys + resolved secret values). Screenshots
  are images and `redaction` masks text, not pixels — so the second guarantee carries them: every
  input, screenshots included, goes only to the provider/endpoint you configured.
- **On-screen secrets stay in the pixels (BE-0151).** Because images cannot be masked, a secret the
  app *displays* — a typed password, an OTP, PII on screen — stays verbatim in the raw pixels of the
  screenshot the AI sees: the live screen every turn during `record`, and the captured failure
  screenshot (if any) during `triage --ai`, read from the run's `runs/` evidence. That image goes to
  the AI provider you configured. Redaction covers the `${secrets.X}`
  *value* wherever it appears in text (network, element tree, logs), not what the app renders on
  screen. So that the exposure is never a surprise, `record` and `triage --ai` print a one-time warning when
  the target binds `secrets:`. This warning is a disclosure, not a mitigation (visual evidence is the point):
  to avoid the exposure entirely, skip AI-driven authoring for a secret-bearing flow, or keep the
  secret off-screen in the app under test.
- **Usage and cost are recorded to an attributed ledger**
  ([BE-0196](../roadmaps/BE-0196-ai-usage-cost-ledger/BE-0196-ai-usage-cost-ledger.md)). Every AI call
  appends one line to a JSON Lines (JSONL) ledger tagged with what its tokens were spent on (command,
  provider, model, scenario) and priced in dollars where the provider has per-token pricing. It is
  reporting only — recording is best-effort and never touches the deterministic `run` verdict. Two
  optional fields under `ai` tune it:

  ```yaml
  defaults:
    ai:
      usageLedger: runs/usage.jsonl              # optional: ledger path (default runs/usage.jsonl; "" disables)
      pricing:                                   # optional: override the shipped per-token rates (USD per million tokens)
        api-key/sonnet: { input: 3.0, output: 15.0, cacheWrite: 3.75, cacheRead: 0.3 }
  ```

  `usageLedger` sets the JSONL path — the default is `runs/usage.jsonl` (under the gitignored `runs/`
  tree), and an explicit empty string turns persistence off. `pricing` overrides the shipped default
  rate table, keyed by `"provider/model"` (the model part matches a model id by family, e.g.
  `api-key/sonnet` prices any `claude-sonnet-*`); a subscription provider with no per-token price
  (`ant`, `claude-code`) records the token counts with a null cost rather than a fabricated dollar
  figure. Like the textual inputs above, the ledger stores counts, prices, and labels only — never
  prompt or response content.

### Mailbox (the `email` step)

`targets.<name>.mailbox` configures the generic HTTP mailbox the [`email`](scenarios.md#email-poll-a-mailbox-for-a-received-code)
step polls for a 2FA / verification code, so the endpoint and credentials live in config (not the
scenario):

```yaml
targets:
  myapp:
    mailbox:
      kind: http                                          # transport adapter; defaults to http when omitted
      url: "${secrets.MAILBOX_URL}"                       # inbox endpoint (GET); ${secrets.*} resolved at run time
      headers: { Authorization: "Bearer ${secrets.MAILBOX_TOKEN}" }
      # Optional response mapping, to read any provider's JSON without per-provider code:
      messages: "items"                                   # dotted path to the message array (default: the response is the array)
      fields: { to: to, subject: subject, body: text, receivedAt: receivedAt, id: id }
```

The defaults match the common shape (an array of messages with `to` / `subject` / `body` /
`receivedAt` / `id`), so a conforming API needs no `messages` / `fields` mapping. The `email` step
reads the inbox over HTTP, keeps only messages newer than the step's start (keyed on `id`), waits
for one that matches, and extracts the code — deterministic and LLM-free
([BE-0046](../roadmaps/BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)).

`kind` selects the transport adapter behind the mailbox — a mailbox is a backend behind one
interface, keyed by transport (`http`, later `imap`) rather than by vendor, so adding a transport
registers an adapter instead of branching the runner
([BE-0186](../roadmaps/BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md)). It
is optional and defaults to `http`, so an existing `mailbox:` block is unchanged; an unknown `kind`
fails the run with a clean config error rather than falling back. Only `http` ships today — it keys
on transport, not on the mail vendor, because vendors differ only in JSON field names, which
`fields` already absorbs.

### Orgs (`orgs:`, the multi-tenant server backend)

`orgs:` declares tenants for the hosted server backend ([BE-0015](../roadmaps/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)).
Each org lists its members — explicit GitHub logins (`members`) and/or whole GitHub orgs
(`githubOrgs`) — and the targets it owns:

```yaml
orgs:
  acme:
    members: [alice, bob]    # explicit GitHub logins
    githubOrgs: [acme-gh]    # everyone in this GitHub org (needs the read:org OAuth scope)
    targets: [demo, checkout]
```

At OAuth login users are assigned their org — an explicit `members` entry first, else a `githubOrgs`
match from their GitHub org memberships. Afterward they see only that org's targets, and a run's
artifacts/scenarios/baselines live under the org's own object-store prefix. A login or target named in
no org falls into the single `default` org, so a config **without** an `orgs:` block is
single-tenant — the CLI and local `serve` ignore `orgs:` entirely.

## Selecting from the CLI

Every command in the CLI (command-line interface) selects one app with `--target <name>` and points at
config with `--config` (default `bajutsu.config.yaml`). `--backend ios` (or a comma list of
platforms/actuators) overrides the resolved order ([cli](cli.md)).

### Cross-browser matrix (`--browsers`, BE-0076)

`bajutsu run --browsers chromium,firefox,webkit` runs the selected scenarios once per engine and
emits a single **engine × scenario pass/fail matrix** — the multi-engine spelling of the `--browser`
axis (web backend only; `--browsers chromium` is exactly `--browser chromium`, and a single engine
takes the ordinary single-engine path). The run is **green only if every requested engine passes
every scenario** (all-must-pass); a scenario green on Chromium and Firefox but red on WebKit is a
machine-detected rendering-engine incompatibility — the kind of "works in Chrome, broken in Safari"
bug a single-engine test can never see. The verdict is purely the existing deterministic per-engine
`run` outcomes aggregated; no AI enters it.

Each engine is a full pass against its own browser pool, so its evidence lands under
`runs/<id>/<engine>/<NN-scenario>/` (no collisions between engines). The run then assembles **one**
`manifest.json`, `junit.xml`, and `report.html` at the run root: the manifest carries a `matrix`
block aggregating the per-engine verdicts, the report renders the engine × scenario grid, and JUnit
keys the engine into each case (`classname="bajutsu.<engine>"`) so CI sees `chromium.login` and
`webkit.login` as distinct cases ([reporting](reporting.md#manifestjson)). An unknown engine in the
list exits 2 before any browser launches, the same as `--browser`. All three engines run headless on
Linux, so the matrix runs inside the ordinary gate with no Mac or device farm; the firefox/webkit
binaries are installed on demand.

### Config from a Git repository (BE-0063)

`--config` also accepts a **Git source**, so a command can run a test repository's suite without a
local checkout — `bajutsu run --config github:acme/mobile-tests@v1.4.0:e2e/bajutsu.config.yaml --target checkout`:

```
github:<owner>/<repo>[@<ref>][:<path>]                          # GitHub shorthand
git+https://<host>/<owner>/<repo>.git[@<ref>][#<path>]          # general form (host reserved)
```

- **GitHub is the only host implemented today.** The general `git+https://<host>/…` form is parsed
  (the door is open for GitHub Enterprise / GitLab later), but a non-`github.com` host currently
  fails with a clear error rather than silently hitting github.com.
- A run from a Git source **records the resolved commit** in its `manifest.json` provenance
  (`configSource: { host, owner, repo, ref, sha }`), so a branch-based run states the exact commit it
  executed and is reproducible after the fact ([reporting](reporting.md#manifestjson)).
- `<ref>` is a branch, tag, or commit SHA (default: the repo's default branch); `<path>` is the
  config within the repo (default: `bajutsu.config.yaml` at the root). A value with no recognized
  scheme is a **local path**, exactly as before.
- Bajutsu resolves the ref to an immutable commit SHA, materializes that subtree into a
  content-addressed cache (`~/.cache/bajutsu/gitsrc/<host>/<owner>/<repo>/<sha>/`), and loads the
  config from it. The config's relative `scenarios` / `baselines` / `schemas` / `appPath` resolve
  **against the checkout root** — the same "relative to where the config lives" rule a local config
  follows against its own directory, except the anchor is the fetched tree's root — so the whole tree
  comes along, not just the YAML. A fetched config is untrusted, so its paths are also **confined** to
  the checkout: an absolute or `../`-escaping value is refused. A local file, being operator-trusted,
  resolves against the config file's own directory and is *not* confined (it may point at a sibling).
- A fresh checkout holds **no built binary**, and there is no local "first" in which to build one, so a
  Git-sourced `run` **builds the app on demand**: when `appPath` is set but missing, it runs the
  config's `build` command from the **checkout root** (where `build`'s relative parts, e.g.
  `make -C demos/showcase swiftui-build`, are rooted), then proceeds. A failed build exits cleanly.
  A local-path `run` is unchanged (it never builds; a missing binary still errors).
- A **pinned commit SHA** (`@<sha>`) is reproducible and runs offline after the first fetch; a branch
  (or tag) is resolved fresh each load.
- **A private repository needs a credential**
  ([BE-0224](../roadmaps/BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md)).
  The token is resolved **per fetch** (so a rotated secret needs no restart), in this order: a
  configured **GitHub App installation** (`BAJUTSU_GITHUB_APP_ID` plus a private key), then a
  serve-entered credential (`BAJUTSU_GIT_CONFIG_TOKEN`), then `GITHUB_TOKEN` / `GH_TOKEN`, then
  `gh auth token`, else anonymous. It is never logged. Grant **least privilege**: prefer a
  **fine-grained** personal access token
  (PAT) — or an App installation — scoped to just the target repositories with the **Contents: read**
  permission, over a classic broad-`repo` PAT that grants read/write to *every* private repo. An
  unattended, self-hosted `serve` should authenticate as a **GitHub App** (a short-lived,
  per-installation token tied to the service, not a person) — see
  [self-hosting → private-repository access](self-hosting.md#private-repository-access-for-the-git-config-source-be-0224).
  When access is missing, the fetch fails with a message that names the *real* cause — a rate limit,
  an organization single sign-on (SSO) authorization gap, a rejected token, or "provide a credential
  with Contents: read for `<owner>/<repo>`" — rather than a bare 404.
- `bajutsu run` takes two gate switches: **`--config-offline`** uses the cache and never touches the
  network (it needs a pinned `@<sha>`, since a branch can't be resolved offline), and
  **`--require-pinned-config`** fails unless the Git config pins a commit SHA — a branch or even a tag
  can move under a gate, so only a SHA is accepted.
- The serve UI also binds a Git source — `serve --config github:…` at startup, or the "From a Git
  repository" field in the "Open config" dialog — materializing the checkout and serving from its
  root ([cli → serve](cli.md#serve)). For a **private** repository the dialog has a credential field
  (BE-0224): enter a fine-grained PAT or App token and it is stored **write-once** through serve's
  secret store — masked, never echoed back (held in the process environment on a local serve;
  encrypted per organization on the hosted backend). A missing-access diagnostic is shown inline in
  the dialog.
- Remaining follow-ups: read-only Git input for `record` / `crawl` (an authored artifact goes to a
  local `--out`, never into the SHA-keyed cache).

## Onboarding a new target

To add a new app, add **app-side preparation and one config entry**. The tool itself needs no changes.

1. **Apply the implementation convention** — `accessibilityIdentifier` on key elements (in the
   app's namespace), expose state in label / traits / value, launch hooks, disable animations.
2. **Add `targets.<name>`** — `bundleId` (required) / `deeplinkScheme` / default `launchEnv` /
   `idNamespaces`, etc.
3. **(Optional) a reusable prelude** — factor login etc. into a `setup:` scenario whose steps run
   before each scenario's own (set per app or per scenario).
4. **Verify with `bajutsu doctor --target <name>`** — look at the convention score (below).
5. **Place scenarios** — write identifiers in the app's namespace.

## Identifier naming convention

`accessibilityIdentifier` is **dot-separated `<namespace>.<element>`**. All lowercase, each segment
`[a-z0-9-]`. The first segment is the namespace, one of the set declared in `idNamespaces`.

```
settings.reindex            # <namespace=settings>.<element=reindex>
home.search
list.row.<id>               # dynamic rows: the suffix is a "data-derived stable key" (index-based is forbidden)
```

Three invariants:

1. **Unique within a screen** — never put the same id twice on one screen
   ([ambiguity detection in selectors](selectors.md#resolution-semantics)). Repeated elements are
   disambiguated by a data-derived key (`list.row.3`). Set operations use `idMatches` + `count`.
2. **Non-localized, data-derived** — do not use display text in an id (it breaks under translation).
3. **Namespace-prefixed** — every id starts with a declared namespace.

The showcase's id catalog is in [showcase](showcase.md) (and, in full, `demos/showcase/SPEC.md`).

## doctor (the convention score)

Implementation: `bajutsu/doctor.py`. **AI-independent and deterministic.** It analyzes one screen's
`query()` (the CLI uses the screen obtained via the actuator) and produces a score.

> `doctor` runs a **runnability gate** first (`preflight.py`), then the score. The gate checks what
> the chosen backend needs: the iOS (XCUITest) backend needs `xcodebuild` / `xcrun`
> plus a booted Simulator; the web (Playwright) backend needs the Playwright package and its Chromium
> browser (`uv sync --extra web` + `playwright install chromium`). It then scores the current screen:
> for a web target it navigates a fresh browser to the target's `baseUrl` and scores that page; for
> iOS it scores the screen on the booted Simulator. The score still covers only the currently
> displayed screen (entry / current screen, not all screens).

### Metrics (`Score`)

Measured over actionable elements (trait ∈ `ACTIONABLE_TRAITS` = button / link / textField /
searchField / textView / switch / slider / tab / cell).

| Metric | Definition | Threshold (default) |
|---|---|---|
| `idCoverage` | fraction of actionable elements with an id | ✓ ≥ 0.9 / warn 0.7–0.9 / fail < 0.7 |
| `namespaceConformance` | fraction of ids whose first segment is in `idNamespaces` | off-convention ids listed in `off_namespace` |
| `duplicateIds` | number of duplicate ids on one screen | Blocked if any |

### Grading

- **Blocked**: no actionable elements on the screen (most likely blank, not yet loaded, or the
  wrong screen — `render` says so), any duplicate id, **or** `idCoverage` < `idCoverageFail` (default 0.7).
- **Ready**: `idCoverage` ≥ `idCoverageOk` (default 0.9) **and** `namespaceConformance` == 1.0.
- **Partial**: otherwise (runnable, but a forecast of coordinate fallback / flakiness).

### Configurable thresholds (`defaults.doctor`, BE-0024)

The id-coverage thresholds that determine the grade are configurable in `defaults.doctor`. Teams
with many decorative elements that legitimately lack test IDs can tune the thresholds for leniency
(typically lowering `idCoverageOk` and/or `idCoverageFail`) without changing the tool:

```yaml
defaults:
  doctor:
    idCoverageOk:   0.85   # default 0.9 — coverage >= this is eligible for "Ready"
    idCoverageFail: 0.6    # default 0.7 — coverage < this drops to "Blocked"
```

Both values must be in [0, 1] and `idCoverageOk` must be >= `idCoverageFail`; an invalid value is
rejected at config load. When omitted, the hardcoded defaults (0.9 / 0.7) apply — existing configs
are unchanged.

### Output

`render(score)` returns a human-readable summary. Missing elements are **listed concretely** so you
can see exactly where to add an id:

```
grade: Partial
idCoverage: 0.83 (5/6)
namespaceConformance: 1.00
duplicateIds: 0
  missing id: label='Close' traits=['button'] frame=(...)
```

The CLI's `doctor` exits with code 1 when the grade is Blocked ([cli](cli.md#doctor)).
