**English** · [日本語](ja/configuration.md)

# Configuration, onboarding a target, and doctor

The tool core is app-agnostic. All app-specific differences belong in config, allowing multiple apps to run with the same binary and the same drivers. Adding a target means adding one `targets.<name>` entry.

Implementation: `bajutsu/config.py` (resolution) · `bajutsu/doctor.py` (convention score). No config ships in the repo root; pass one with `--config` (default filename `bajutsu.config.yaml`) — the demos ship ready-to-run configs, e.g. [`demos/features/demo.config.yaml`](../demos/features/demo.config.yaml) (iOS) and [`demos/web/demo.config.yaml`](../demos/web/demo.config.yaml) (web).

Related: [app-agnostic in concepts](concepts.md#6-app-agnostic-push-differences-into-config) · [drivers](drivers.md) · [scenarios](scenarios.md)

---

## Config layering (defaults × targets)

`bajutsu.config.yaml` has two layers. The resolution order is **defaults < target < scenario** (the
one closer to the test wins).

```yaml
defaults:                       # shared across all targets
  backend: [ios]                # ordered list of platforms (ios/android/web/fake) or actuators (idb); a single string is also OK
  device:  "iPhone 15"
  locale:  en_US
  capture: [screenshot.after, elements, actionLog]
  redact:  { headers: [Authorization, Cookie], fields: [token, password] }
  secrets: [LOGIN_PASSWORD]         # env var names usable as ${secrets.X} (values masked in evidence)
  reservedNamespaces: [auth, nav]   # the id contract for shared flows / components (informational)

targets:
  sample:                       # ← selected by --target sample
    bundleId:       com.bajutsu.sample     # iOS target (required unless baseUrl is set for web)
    deeplinkScheme: bajutsusample
    idNamespaces:   [home, list, counter, settings, onboarding, auth, nav, comp, ctrl, text, lists]
    launchEnv:      { SAMPLE_UITEST: "1" }
    scenarios:      demos/features/app/scenarios   # this target's scenarios dir (run reads it; record writes here)
    # optional: backend / device / locale / launchArgs / setup / redact / secrets / mockServer / appPath / build

  web:                          # a web target (Playwright backend) is identified by URL
    baseUrl:   "http://127.0.0.1:8787/index.html"   # required for web (instead of bundleId)
    backend:   [web]
    headless:  true                                 # web only: false = a visible (headed) browser; --headed overrides per run
    scenarios: demos/web/scenarios
```

A target entry needs **either** `bundleId` (iOS) **or** `baseUrl` (web) — a config with neither is
rejected at load. See [drivers → Playwright](drivers.md#playwright-web) and `demos/web`.

### Resolution (`resolve` → `Effective`)

`resolve(config, target)` builds the effective values `Effective` (a frozen dataclass) for one target.
An undefined target raises `KeyError` (the CLI exits with code 2).

| `Effective` field | Source | Notes |
|---|---|---|
| `bundle_id` | app | iOS target; required unless `base_url` is set |
| `base_url` | app | web target URL (Playwright backend); required for web instead of `bundle_id` |
| `headless` | app | web backend only: `true` (default) runs headless; `false` shows a visible (headed) browser, in slow-motion. `bajutsu run --headed / --no-headed` and the Web UI's "show browser" toggle override per run; iOS ignores it |
| `launch_server` | app | optional `launchServer: {cmd, readyUrl, readyTimeout, cwd, env}` — bring up `baseUrl`'s host for the run, then tear it down: probe `readyUrl` (default `baseUrl`), reuse it if already serving, else run `cmd` and wait until ready (a condition wait, never a fixed sleep). The web analogue of `build` ([BE-0059](../roadmaps/implemented/BE-0059-launch-target-server/BE-0059-launch-target-server.md)) |
| `deeplink_scheme` | app | the scheme used by the preconditions' deeplink |
| `backend` | app ?? defaults | stability-ordered list of platforms (`ios`/`android`/`web`/`fake`) or actuators (`idb`); a single string is listified ([drivers](drivers.md#backend-selection-and-the-actuator)) |
| `device` / `locale` | app ?? defaults | `locale` is applied at launch (`simctl` launch args) |
| `launch_env` / `launch_args` | app | merged/appended by preconditions at run time |
| `id_namespaces` | app | referenced by doctor |
| `reserved_namespaces` | defaults | informational (doctor scores against the app's `idNamespaces` only) |
| `mock_server` | app | ⚠️ schema only · not wired |
| `setup` | app | default reusable prelude (a scenario whose steps run before each scenario's own) |
| `scenarios` | app | this app's scenarios dir — `run --target` loads every `*.yaml` here; `record` writes new ones here. Relative to the run's cwd. `run --scenario` / `record --out` override it |
| `capture` | defaults | the default evidence ([the note in evidence](evidence.md#three-ways-to-request-evidence)) |
| `redact` | defaults ∪ app | merged (below) |
| `secrets` | defaults ∪ app | env var names declaring `${secrets.X}`; values are masked in evidence ([evidence](evidence.md#masking-redact)) |

The `backend` field validator `_norm` normalizes "a single string → a 1-element list" (on both
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

### Orgs (`orgs:`, the multi-tenant server backend)

`orgs:` declares tenants for the hosted server backend ([BE-0015](../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)).
Each org lists its members — explicit GitHub logins (`members`) and/or whole GitHub orgs
(`githubOrgs`) — and the targets it owns:

```yaml
orgs:
  acme:
    members: [alice, bob]    # explicit GitHub logins
    githubOrgs: [acme-gh]    # everyone in this GitHub org (needs the read:org OAuth scope)
    targets: [demo, checkout]
```

At OAuth login a user is assigned their org — an explicit `members` entry first, else a `githubOrgs`
match from their GitHub org memberships. Afterward they see only that org's targets, and a run's
artifacts/scenarios/baselines live under the org's own object-store prefix. A login or target named in
no org falls into the single `default` org, so a config **without** an `orgs:` block is
single-tenant — the CLI and local `serve` ignore `orgs:` entirely.

## Selecting from the CLI

Every command in the CLI (command-line interface) selects one app with `--target <name>` and points at
config with `--config` (default `bajutsu.config.yaml`). `--backend ios` (or a comma list of
platforms/actuators) overrides the resolved order ([cli](cli.md)).

### Config from a Git repository (BE-0063)

`--config` also accepts a **Git source**, so a command can run a test repository's suite without a
local checkout — `bajutsu run --config github:acme/mobile-tests@v1.4.0:e2e/bajutsu.config.yaml --target checkout`:

```
github:<owner>/<repo>[@<ref>][:<path>]                          # GitHub shorthand
git+https://<host>/<owner>/<repo>.git[@<ref>][#<path>]          # any Git host
```

- `<ref>` is a branch, tag, or commit SHA (default: the repo's default branch); `<path>` is the
  config within the repo (default: `bajutsu.config.yaml` at the root). A value with no recognized
  scheme is a **local path**, exactly as before.
- Bajutsu resolves the ref to an immutable commit SHA, materializes that subtree into a
  content-addressed cache (`~/.cache/bajutsu/gitsrc/<host>/<owner>/<repo>/<sha>/`), and loads the
  config from it. Because the config's `scenarios` / `baselines` / `schemas` / `appPath` are relative
  paths, they resolve **against the checkout root**, not the caller's working directory — so the whole
  tree comes along, not just the YAML.
- A **pinned ref** (`@<tag>` / `@<sha>`) is reproducible and offline after the first fetch; a bare
  branch is resolved fresh each load. Private repos use a token from `GITHUB_TOKEN` / `GH_TOKEN`, else
  `gh auth token`; the token is never logged.
- This first slice covers the CLI read path (`run` / `doctor`). Recording the resolved SHA as run
  provenance, the `--config-offline` / `--require-pinned-config` switches, and the serve "from Git"
  picker are follow-ups.

## Onboarding a new target

To add a new app, add **app-side preparation and one config entry**. No changes to the tool itself are required.

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
2. **Non-localized, data-derived** — don't use display text in an id (it breaks under translation).
3. **Namespace-prefixed** — every id starts with a declared namespace.

The sample app's id catalog is in [sample-app](sample-app.md#accessibilityidentifier-catalog).

## doctor (the convention score)

Implementation: `bajutsu/doctor.py`. **AI-independent and deterministic.** It analyzes one screen's
`query()` (the CLI uses the screen obtained via the actuator) and produces a score.

> `doctor` runs a **runnability gate** first (`preflight.py`), then the score. The gate checks what
> the chosen backend needs: the iOS (idb) backend needs the CLIs `xcrun` and `idb` / `idb_companion`
> plus a booted Simulator; the web (Playwright) backend needs the Playwright package and its Chromium
> browser (`uv sync --extra web` + `playwright install chromium`). It then scores the current screen:
> for a web target it navigates a fresh browser to the target's `baseUrl` and scores that page; for
> iOS it scores the screen on the booted Simulator. The score still covers only the currently
> displayed screen (entry / current screen, not all screens).

### Metrics (`Score`)

Measured over actionable elements (trait ∈ `ACTIONABLE_TRAITS` = button / link / textField /
searchField / textView / switch / slider / tab / cell).

| Metric | Definition | Threshold |
|---|---|---|
| `idCoverage` | fraction of actionable elements with an id | ✓ ≥ 0.9 / warn 0.7–0.9 / fail < 0.7 |
| `namespaceConformance` | fraction of ids whose first segment is in `idNamespaces` | off-convention ids listed in `off_namespace` |
| `duplicateIds` | number of duplicate ids on one screen | Blocked if any |

### Grading

- **Blocked**: any duplicate id **or** `idCoverage` < 0.7.
- **Ready**: `idCoverage` ≥ 0.9 **and** `namespaceConformance` == 1.0.
- **Partial**: otherwise (runnable, but a forecast of coordinate fallback / flakiness).

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
