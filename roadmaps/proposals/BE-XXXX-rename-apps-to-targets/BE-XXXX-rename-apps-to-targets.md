**English** · [日本語](BE-XXXX-rename-apps-to-targets-ja.md)

# BE-XXXX — Rename the config `apps` key to `targets`

* Proposal: [BE-XXXX](BE-XXXX-rename-apps-to-targets.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: Platform expansion (Android / Web / Flutter)

## Introduction

Bajutsu groups everything app-specific under a single config key, `apps.<name>`, and every
command selects one with `--app <name>` ([DESIGN §8](../../../DESIGN.md),
[configuration.md](../../../docs/configuration.md)). That naming dates from the
iOS-Simulator-only scope, where the thing under test was always an iOS app. The Web (Playwright)
backend has since landed ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)),
and a web target is a URL, not an "app" — the schema already carries `baseUrl` beside `bundleId`
for exactly that case. This item renames the grammar from `apps` to `targets` (and `--app` to
`--target`) so the term names what it actually holds: the thing under test, on any platform.

## Motivation

`apps` has become a misnomer that the code itself already works around:

- The per-entry model `AppConfig` documents the split in its own comments — *"iOS apps identify
  the target by bundleId; web apps by baseUrl instead"* — and its validator rejects a malformed
  entry with *"app needs bundleId (iOS) or baseUrl (web)"*. The code reaches for the word
  **target** to describe the concept while the key stays `apps`.
- With Android planned ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) and the
  scope statement itself due to move to multi-platform
  ([BE-0010](../BE-0010-update-scope-statement/BE-0010-update-scope-statement.md)), calling a
  website or an Android package an "app" only gets more strained.
- App-agnosticism is a prime directive ([DESIGN §2](../../../DESIGN.md)): per-target differences
  live in config and the tool stays unchanged across them. `targets.<name>` states that contract
  in platform-neutral terms; `apps.<name>` quietly presumes iOS.

"Target" is the conventional word for the unit under test in test tooling (the *system under
test*), it is platform-neutral, and it is already the word the code and docs reach for ("the
target app"). Renaming now — while the config surface is one key and the project is pre-1.0 — is
far cheaper than after the term has spread across three platforms' worth of docs and scenarios.

## Detailed design

### What changes

A coherent, full rename of the *generic container and selector* term across every layer.
Resolved as a **hard cutover** with no compatibility alias: the repo's own configs, tests, and
docs all move in the same change.

| Layer | Today | After |
|---|---|---|
| Config key (root) | `apps:` mapping | `targets:` mapping |
| Config key (org) | `orgs.<o>.apps: [...]` (target names an org owns) | `orgs.<o>.targets: [...]` |
| Schema model | `class AppConfig` | `class TargetConfig` |
| Schema fields | `Config.apps`, `OrgConfig.apps`, `Effective.app` | `Config.targets`, `OrgConfig.targets`, `Effective.target` |
| Resolution fns | `resolve(config, app)`, `org_for_app`, `apps_for_org` | `resolve(config, target)`, `org_for_target`, `targets_for_org` |
| CLI flag | `--app <name>` on `run` / `record` / `crawl` / `doctor` / `codegen` / `triage` | `--target <name>` |
| CLI param | `app_name` | `target_name` |
| serve HTTP | `GET /api/apps` | `GET /api/targets` |
| serve helpers | `list_apps`, `app_build_info`, `app_scenarios_dir`, `_app_forbidden`, `list_apps_payload` | the `target`-named equivalents |
| serve.js | `#app` / `#rec-app` / `#crawl-app`, `/api/apps` fetch | `#target` / …, `/api/targets` |
| MCP tools | `bajutsu_run(app=…)`, `bajutsu_doctor(app=…)` | `target=…` |
| Error / help text | "unknown app …", "define apps.\<x\>", "(set apps.\<x\>.scenarios…)" | "unknown target …", "targets.\<x\>" |
| Example configs | `apps:` in `demos/*/…config.yaml`, `tests/resources/…` | `targets:` |
| Docs (en + ja) | `apps.<name>`, `--app`, "per-app" across the documentation set | `targets.<name>`, `--target`, "per-target" |

### What does *not* change

Only the **generic** term moves. Platform-specific field names that legitimately name an iOS
concept stay, because renaming them would mislabel rather than clarify:

- `bundleId`, `appPath`, `deeplinkScheme` name iOS specifics (a bundle identifier, a path to a
  built `.app`, a URL scheme) — not the generic unit under test. A web target simply omits them
  and sets `baseUrl`.
- The `scenarios/<name>/` layout and per-target scenario directories — keyed by the target's
  name, which is unchanged.
- The deterministic core (selector resolution, orchestrator, runner, assertions) — untouched.
  This is a naming change at the config / CLI boundary only.

### Migration (hard cutover)

There is no `apps:` / `targets:` dual-accept window. The change is mechanical for any external
config:

- `apps:` → `targets:` (and `orgs.<o>.apps:` → `orgs.<o>.targets:`)
- `--app <name>` → `--target <name>` on every command
- `/api/apps` → `/api/targets` for any direct API caller

The updated [configuration.md](../../../docs/configuration.md) /
[cli.md](../../../docs/cli.md) and a one-line release note cover the migration. The blast radius
is small while the project is pre-1.0 and the config surface is a single key.

## Alternatives considered

- **Keep `apps`, just document that web targets reuse it.** Rejected: the term actively misleads
  on web and Android, and the code already narrates "target" in comments and validator messages.
  Documentation cannot fix a key whose name asserts the wrong thing.
- **Add a deprecation alias (accept both, warn on `apps:`).** Rejected per the scoping decision: a
  dual-accept period means two terms in the docs, a warning path to carry, and a deferred removal
  to track — cost that only pays off at a scale of external installs and published scenarios that
  Bajutsu has not reached. A clean cutover now is cheaper than a migration later.
- **Rename the config key only, leave `--app`.** Rejected: a config that says `targets:` driven by
  a CLI that says `--app` is incoherent; the selector flag must match the key it selects.
- **A different word — `subjects` / `systems` / `suts` / `applications`.** Rejected: "target" is
  the established term for the unit under test in E2E tooling, is the shortest platform-neutral
  fit, and is already the word in the codebase, so it minimizes churn and surprise.

## References

- [DESIGN §8](../../../DESIGN.md) (CLI & config: per-app / multi-app), [DESIGN §2](../../../DESIGN.md) (app-agnostic prime directive)
- [configuration.md](../../../docs/configuration.md), [cli.md](../../../docs/cli.md) — the config layering and the `--app` flag
- `bajutsu/config.py` — `AppConfig`, `Config.apps`, `OrgConfig.apps`, `resolve` / `org_for_app` / `apps_for_org`
- Related items: [BE-0010](../BE-0010-update-scope-statement/BE-0010-update-scope-statement.md) (update the scope statement — the multi-platform doc move this rename rides with), [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) (cross-platform abstractions), [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) (web Playwright backend — the landed platform that makes "app" a misnomer), [BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md) (platform-aware backend registry)
