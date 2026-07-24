**English** · [日本語](BE-0318-serve-server-settings-tab-ja.md)

# BE-0318 — Show the running server's configuration and bundled iOS test-runner status in a serve Settings tab

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0318](BE-0318-serve-server-settings-tab.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0318") |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Add a read-only **Server** tab to the `serve` Settings modal that shows how the running server is
configured — its deployment mode, the bound config and where it came from, the
[backends](../../docs/glossary.md#driver-backend-actuator-platform) it can run, and its run-storage,
retention, and concurrency settings — and, most usefully, whether this server ships the bundled iOS
XCUITest test runner. Every value comes from the server state already resolved at launch plus one
filesystem probe, so the tab is Tier-1, read-only, and AI-free: nothing it displays reaches the
`run` verdict.

## Motivation

`serve` resolves a great deal of configuration at launch — from command-line flags, the environment,
and the bound config — but the browser surfaces almost none of it. A user looking at the UI cannot
answer "what is this server actually running with?" without going back to the launch command. The
Settings modal already gathers the two operator-facing concerns it does expose (the AI provider and
the scenario secrets); the server's own configuration has no home there.

The sharpest instance of the gap is the iOS test runner. Since
[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md), an iOS
[Simulator](../../docs/glossary.md#target-app-device) run that names no `xcuitest.testRunner` falls
back to a generic XCUITest runner bundled into the wheel as package data — but only when the wheel
actually ships one, which a plain source checkout and every non-macOS wheel do not. With XCUITest
now the sole iOS backend ([BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)),
the presence of that runner decides whether an iOS Simulator run can start at all. Today the only
way to learn whether it is deployed is to start a run and watch what happens. A one-line "iOS test
runner: deployed (built against Xcode 16.2 / SDK 18.2)" — or "not deployed" — pre-empts that
confusion, and it is exactly the kind of read-only, server-wide fact the Settings modal should
answer.

## Detailed design

Tier-1, read-only, and AI-free by construction: the feature reads the already-resolved `ServeState`
and probes the wheel for the bundled runner. Nothing it shows is a per-run signal, so nothing
touches the deterministic `run` / CI gate (prime directive 1).

The work breaks into four MECE units.

**Unit 1 — a read-only server-info endpoint.** Add `GET /api/server` → `ops.server_settings(state)`
(a new operation beside `config_info` in `bajutsu/serve/operations/config.py`, registered in the
shared route table so both backends dispatch it, [BE-0253](../BE-0253-serve-route-registry-unification/BE-0253-serve-route-registry-unification.md)). It assembles a JSON view of the running
server's resolved configuration: the deployment mode (local vs hosted), the bound config and its
source/provenance (reusing what `config_info` / `config_content` already compute), the backends this
server can run, the runs and baselines directories, the soft-delete retention window, the
concurrency caps (total, per-user, per-org), and the running version string (the open value
`server_version` already returns for BE-0272's header badge). The endpoint's authz posture matches
`config_info` — open to any viewer — so it must expose only what that posture already permits: the
admin-gated commit/branch from `server_checkout` is deliberately *not* surfaced here, because a
branch name can leak an in-progress work topic and `server_checkout` gates it behind an admin call
for exactly that reason. For its own host paths it applies BE-0108's rationale — a hosted user has no
filesystem relationship to the host, so an absolute host path is dead information and needless
exposure ([BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)) —
and withholds the config path and the runs and baselines directories when `state.hosted`. This is
deliberately stricter than `config_info`, which returns its `config` path unconditionally; the
broader server-info surface withholds host paths on hosted rather than copy that. The non-path fields
(deployment mode, backends, retention window, concurrency caps, version) are shown either way, and the
endpoint discloses presence and configuration only, never a secret's plaintext.

**Unit 2 — iOS bundled test-runner status in the payload.** Extend the Unit-1 payload with an
`iosRunner` block sourced from the bundled-runner probe (`bundled_products_dir()` /
`bundled_runner_build_info()`): whether this build ships the bundled generic XCUITest runner
(`bundled_products_dir()` is not `None`), the toolchain metadata it was built against when present
(`bundled_runner_build_info()` → the Xcode and Simulator SDK versions), and whether the bound config
overrides the runner with an explicit `xcuitest.testRunner`. Those probes live in the private
submodule `bajutsu.platform_lifecycle.environments._bundled_runner`, whose package `__init__` asks
callers to import from the package root rather than reach into a platform module; `server_settings`
sits in a different package, so this unit first re-exports the two probes at the
`environments` package root and imports them from there, keeping the operations layer off the private
submodule. Together these answer "will an iOS Simulator run that names no runner find one,
and what was it built against?" without starting a run. The bundled runner is app-agnostic by design
(BE-0292), so this stays server-wide, not per-app. iOS earns a dedicated block now because its
runner is the one server-wide readiness fact whose absence silently blocks a whole backend; the
same shape generalizes to a per-backend readiness map later (is Playwright's browser installed, is
`adb` reachable) rather than accreting a second one-off field — but those facts are not the gap this
item closes, so it names only the iOS runner and leaves that generalization to a follow-up.

**Unit 3 — the Server tab in the Settings modal.** Add a third tab beside AI and Secrets
(`data-settab="server"`, panel `#setpanel-server`) in `bajutsu/templates/serve.html.j2`, wired into
`showSettingsTab` in `serve.core.mjs` exactly as the existing two are. A `loadServerInfo()` fetches
`/api/server` when the modal opens and renders the values as labelled read-only rows, with the iOS
runner shown as a plain "deployed / not deployed" line that names the reason and the build toolchain
when deployed. The tab has no inputs: it reports, it never mutates.

**Unit 4 — tests.** Operation-level tests for `server_settings`: the local-vs-hosted redaction of
host paths, the bundled-vs-absent runner branch (inject a products directory and its absence), and an
`xcuitest.testRunner` override reflected in the payload. Route-registration coverage that
`GET /api/server` dispatches on both backends. All deterministic and device-free — the endpoint reads
state and probes the filesystem, so no Simulator is needed and the core gate still runs on Linux.

## Alternatives considered

- **Fold it into the doctor panel (BE-0148).** Rejected. Doctor answers a *per-target* question — is
  this target runnable, and is its screen addressable? — and needs a booted device and a live screen
  query. The server-settings view is *server-wide*, static, and device-free. Different question,
  different inputs; co-locating them would blur both.
- **Extend the version badge (BE-0272).** Rejected. The badge is a single header affordance for the
  running commit; a multi-value read-only settings surface belongs in the Settings modal beside the
  other operator concerns, not crammed into the header.
- **A new top-level view instead of a Settings tab.** Rejected. This is operator configuration,
  exactly what the Settings modal already groups (provider, secrets). A separate top-level view would
  fragment the "how is this set up?" surface across two places.
- **Show only the iOS runner status.** Considered and declined in favor of the comprehensive
  server-configuration view. The runner is the sharpest instance of a general gap — the browser shows
  almost nothing about the running server's configuration — so the tab addresses the whole gap and
  gives the runner status its most prominent row.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `GET /api/server` → `ops.server_settings(state)` with hosted-aware redaction.
- [x] Unit 2 — `iosRunner` block (bundled-runner presence, build-info, `xcuitest.testRunner` override).
- [x] Unit 3 — the read-only Server tab in the Settings modal (`serve.html.j2` + `serve.core.mjs`).
- [x] Unit 4 — operation and route-registration tests (local/hosted, bundled/absent, override).

## References

- [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md) — bundles the generic
  XCUITest Simulator runner into the wheel; the `_bundled_runner` probe this tab surfaces.
- [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md) — makes
  XCUITest the sole iOS backend, so the runner's presence now decides every iOS Simulator run.
- [BE-0148](../BE-0148-serve-doctor/BE-0148-serve-doctor.md) — the complementary per-target doctor
  readiness panel.
- [BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge.md) — the running commit/version
  badge whose value this tab's version row reuses.
- [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md) —
  the hosted-vs-local redaction pattern the endpoint follows.
