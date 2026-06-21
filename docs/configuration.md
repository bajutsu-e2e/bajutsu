**English** · [日本語](ja/configuration.md)

# Configuration, onboarding an app, and doctor

The tool core is app-agnostic. All app-specific differences belong in config, allowing multiple apps to run with the same binary and the same drivers. Adding an app means adding one `apps.<name>` entry.

Implementation: `bajutsu/config.py` (resolution) · `bajutsu/doctor.py` (convention score) · the root [`bajutsu.config.yaml`](../bajutsu.config.yaml).

Related: [app-agnostic in concepts](concepts.md#6-app-agnostic-push-differences-into-config) · [drivers](drivers.md) · [scenarios](scenarios.md)

---

## Config layering (defaults × apps)

`bajutsu.config.yaml` has two layers. The resolution order is **defaults < app < scenario** (the
one closer to the test wins).

```yaml
defaults:                       # shared across all apps
  backend: [ios]                # ordered list of platforms (ios/android/web/fake) or actuators (idb); a single string is also OK
  device:  "iPhone 15"
  locale:  en_US
  capture: [screenshot.after, elements, actionLog]
  redact:  { headers: [Authorization, Cookie], fields: [token, password] }
  secrets: [LOGIN_PASSWORD]         # env var names usable as ${secrets.X} (values masked in evidence)
  reservedNamespaces: [auth, nav]   # the id contract for shared flows / components (informational)

apps:
  sample:                       # ← selected by --app sample
    bundleId:       com.bajutsu.sample     # required
    deeplinkScheme: bajutsusample
    idNamespaces:   [home, list, counter, settings, onboarding, auth, nav, comp, ctrl, text, lists]
    launchEnv:      { SAMPLE_UITEST: "1" }
    scenarios:      demos/features/app/scenarios   # this app's scenarios dir (run reads it; record writes here)
    # optional: backend / device / locale / launchArgs / setup / redact / secrets / mockServer / appPath / build
```

### Resolution (`resolve` → `Effective`)

`resolve(config, app)` builds the effective values `Effective` (a frozen dataclass) for one app.
An undefined app raises `KeyError` (the CLI exits with code 2).

| `Effective` field | Source | Notes |
|---|---|---|
| `bundle_id` | app | required |
| `deeplink_scheme` | app | the scheme used by the preconditions' deeplink |
| `backend` | app ?? defaults | stability-ordered list of platforms (`ios`/`android`/`web`/`fake`) or actuators (`idb`); a single string is listified ([drivers](drivers.md#backend-selection-and-the-actuator)) |
| `device` / `locale` | app ?? defaults | ⚠️ `locale` is currently not applied at launch |
| `launch_env` / `launch_args` | app | merged/appended by preconditions at run time |
| `id_namespaces` | app | referenced by doctor |
| `reserved_namespaces` | defaults | informational (doctor scores against the app's `idNamespaces` only) |
| `mock_server` | app | ⚠️ schema only · not wired |
| `setup` | app | default reusable prelude (a scenario whose steps run before each scenario's own) |
| `scenarios` | app | this app's scenarios dir — `run --app` loads every `*.yaml` here; `record` writes new ones here. Relative to the run's cwd. `run --scenario` / `record --out` override it |
| `capture` | defaults | the default evidence ([the note in evidence](evidence.md#three-ways-to-request-evidence)) |
| `redact` | defaults ∪ app | merged (below) |
| `secrets` | defaults ∪ app | env var names declaring `${secrets.X}`; values are masked in evidence ([evidence](evidence.md#masking-redact)) |

The `backend` field validator `_norm` normalizes "a single string → a 1-element list" (on both
defaults / app).

### Merging redact

Config's `defaults.redact` and `apps.<name>.redact` are **union**ed (`_merge_redact`, unioning
`labels`/`headers`/`fields` individually). The scenario's `redact`
([evidence](evidence.md#masking-redact)) layers on top.

### Secrets (`secrets:`)

`secrets:` (a list of **environment-variable names**, declared in `defaults` and/or `apps.<name>`,
unioned by `resolve`) is the declaration site for the `${secrets.X}` variables a scenario can input.
At run time `bajutsu run` resolves each declared name from the environment, interpolates its value
into the action (`${secrets.X}`), and **masks the literal value everywhere it would appear in
evidence** ([evidence](evidence.md#masking-redact)). The scenario source keeps the `${secrets.X}`
token, never the value.

### Orgs (`orgs:`, the multi-tenant server backend)

`orgs:` declares tenants for the hosted server backend ([BE-0015](../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)).
Each org lists its member GitHub logins and the apps it owns:

```yaml
orgs:
  acme:
    members: [alice, bob]
    apps: [demo, checkout]
```

At OAuth login a user is assigned their org; afterward they see only that org's apps, and a run's
artifacts/scenarios/baselines live under the org's own object-store prefix. A login or app named in
no org falls into the single `default` org, so a config **without** an `orgs:` block is
single-tenant — the CLI and local `serve` ignore `orgs:` entirely.

## Selecting from the CLI

Every command in the CLI (command-line interface) selects one app with `--app <name>` and points at
config with `--config` (default `bajutsu.config.yaml`). `--backend ios` (or a comma list of
platforms/actuators) overrides the resolved order ([cli](cli.md)).

## Onboarding a new app

To add a new app, add **app-side preparation and one config entry**. No changes to the tool itself are required.

1. **Apply the implementation convention** — `accessibilityIdentifier` on key elements (in the
   app's namespace), expose state in label / traits / value, launch hooks, disable animations.
2. **Add `apps.<name>`** — `bundleId` (required) / `deeplinkScheme` / default `launchEnv` /
   `idNamespaces`, etc.
3. **(Optional) a reusable prelude** — factor login etc. into a `setup:` scenario whose steps run
   before each scenario's own (set per app or per scenario).
4. **Verify with `bajutsu doctor --app <name>`** — look at the convention score (below).
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

> `doctor` runs a **runnability gate** first (`preflight.py`: the required CLIs for the actuator
> — `xcrun`, and `idb` / `idb_companion` for idb — plus a booted Simulator), then the score. The
> score still covers only the currently displayed screen (entry / current screen, not all screens).

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
