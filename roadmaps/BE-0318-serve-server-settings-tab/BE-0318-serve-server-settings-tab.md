**English** · [日本語](BE-0318-serve-server-settings-tab-ja.md)

# BE-0318 — Show the running server's configuration and bundled iOS test-runner status in a serve Settings tab

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0318](BE-0318-serve-server-settings-tab.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0318") |
| Implementing PR | [#1323](https://github.com/bajutsu-e2e/bajutsu/pull/1323) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->


## Introduction

Add a read-only **Server** tab to the `serve` Settings modal. The tab shows how the running server is
configured:

- the deployment mode
- the bound config and where the config came from
- the [backends](../../docs/glossary.md#driver-backend-actuator-platform) the server can run
- the run-storage, retention, and concurrency settings

The tab also shows whether this server ships the bundled iOS XCUITest test runner, which is the most
useful fact of all. Every value comes from the server state that launch already resolved, plus one
filesystem probe. The tab is therefore Tier-1, read-only, and AI-free. Nothing the tab displays
reaches the `run` verdict.

## Motivation

`serve` resolves a great deal of configuration at launch, from command-line flags, the environment,
and the bound config. The browser surfaces almost none of it. A user looking at the UI cannot answer
"what is this server actually running with?" without going back to the launch command. The Settings
modal already gathers the two operator-facing concerns that the modal exposes: the AI provider and
the scenario secrets. The server's own configuration has no home there.

The sharpest instance of the gap is the iOS test runner. Since
[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md), an iOS
[Simulator](../../docs/glossary.md#target-app-device) run that names no `xcuitest.testRunner` falls
back to a generic XCUITest runner. The wheel bundles that runner as package data. The fallback works
when the wheel actually ships a runner. A plain source checkout and every non-macOS wheel do not ship
one.

XCUITest is now the sole iOS backend
([BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)).
The presence of the runner therefore decides whether an iOS Simulator run can start at all. Today
the only way to learn whether the runner is deployed is to start a run and watch what happens.

A one-line "iOS test runner: deployed (built against Xcode 16.2 / SDK 18.2)", or "not deployed",
pre-empts that confusion. The line is a read-only, server-wide fact, which the Settings modal should
answer.

## Detailed design

The feature is Tier-1, read-only, and AI-free by construction. The feature reads the already-resolved
`ServeState` and probes the wheel for the bundled runner. Nothing the feature shows is a per-run
signal, so nothing touches the deterministic `run` / CI gate (prime directive 1).

The work breaks into four MECE units.

**Unit 1: a read-only server-info endpoint.** Add `GET /api/server` → `ops.server_settings(state)`.
The operation is new and sits beside `config_info` in `bajutsu/serve/operations/config.py`. The
operation is registered in the shared route table, so both backends dispatch it
([BE-0253](../BE-0253-serve-route-registry-unification/BE-0253-serve-route-registry-unification.md)).

The operation assembles a JSON view of the running server's resolved configuration. The view holds:

- the deployment mode (local or hosted)
- the bound config and its source and provenance, reusing what `config_info` and `config_content`
  already compute
- the backends this server can run
- the runs and baselines directories
- the soft-delete retention window
- the concurrency caps (total, per-user, per-org)
- the running version string, which is the open value that `server_version` already returns for
  BE-0272's header badge

The authz posture of the endpoint matches `config_info`: the endpoint is open to any viewer. The
endpoint must therefore expose purely what that posture already permits. The admin-gated
commit and branch from `server_checkout` are deliberately *not* surfaced here. A branch name can
leak an in-progress work topic, and `server_checkout` gates the branch behind an admin call for
exactly that reason.

For the endpoint's own host paths, the endpoint applies the rationale of
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md).
A hosted user has no filesystem relationship to the host, so an absolute host path is dead
information and needless exposure. The endpoint withholds the config path and the runs and baselines
directories when `state.hosted`.

This rule is deliberately stricter than `config_info`, which returns its `config` path
unconditionally. The broader server-info surface withholds host paths on hosted servers instead of
copying that behavior. The non-path fields (deployment mode, backends, retention window, concurrency
caps, version) appear either way. The endpoint discloses presence and configuration, never a
secret's plaintext.

**Unit 2: iOS bundled test-runner status in the payload.** Extend the Unit 1 payload with an
`iosRunner` block. The block comes from the bundled-runner probe (`bundled_products_dir()` and
`bundled_runner_build_info()`). The block reports three facts:

- Whether this build ships the bundled generic XCUITest runner (`bundled_products_dir()` is not
  `None`).
- The toolchain metadata the runner was built against, when present (`bundled_runner_build_info()`
  returns the Xcode and Simulator SDK versions).
- Whether the bound config overrides the runner with an explicit `xcuitest.testRunner`.

The probes live in the private submodule `bajutsu.platform_lifecycle.environments._bundled_runner`.
The package `__init__` of that submodule asks callers to import from the package root, instead of
reaching into a platform module. The function `server_settings` sits in a different package. This
unit therefore first re-exports the two probes at the `environments` package root and imports them
from there. The re-export keeps the operations layer off the private submodule.

Together the facts answer "will an iOS Simulator run that names no runner find one, and what was the
runner built against?" without starting a run. The bundled runner is app-agnostic by design
(BE-0292), so the information stays server-wide, not per-app.

iOS earns a dedicated block now because the iOS runner is the one server-wide readiness fact whose
absence silently blocks a whole backend. The same shape generalizes to a per-backend readiness map
later (is Playwright's browser installed, is `adb` reachable). The generalization would replace a
second one-off field. Those facts are not the gap this item closes, so the item names the iOS runner
and leaves the generalization to a follow-up.

**Unit 3: the Server tab in the Settings modal.** Add a third tab beside AI and Secrets
(`data-settab="server"`, panel `#setpanel-server`) in `bajutsu/templates/serve.html.j2`. Wire the tab
into `showSettingsTab` in `serve.core.mjs` exactly as the existing two tabs are wired.

A `loadServerInfo()` function fetches `/api/server` when the modal opens. The function renders the
values as labelled read-only rows. The iOS runner appears as a plain "deployed / not deployed" line.
When the runner is deployed, the line names the reason and the build toolchain. The tab has no
inputs. The tab reports and never mutates.

**Unit 4: tests.** The tests cover two areas.

- Operation-level tests for `server_settings`:
  - The local-versus-hosted redaction of host paths.
  - The bundled-versus-absent runner branch, injecting a products directory and its absence.
  - An `xcuitest.testRunner` override reflected in the payload.
- Route-registration coverage that `GET /api/server` dispatches on both backends.

All tests are deterministic and device-free. The endpoint reads state and probes the filesystem, so
no Simulator is needed, and the core gate still runs on Linux.

## Alternatives considered

- **Fold the view into the doctor panel (BE-0148).** Rejected. Doctor answers a *per-target*
  question: is this target runnable, and is its screen addressable? Doctor needs a booted device and
  a live screen query. The server-settings view is *server-wide*, static, and device-free. The two
  views ask different questions with different inputs. Co-locating them would blur both.
- **Extend the version badge (BE-0272).** Rejected. The badge is a single header affordance for the
  running commit. A multi-value read-only settings surface belongs in the Settings modal beside the
  other operator concerns, not crammed into the header.
- **A new top-level view instead of a Settings tab.** Rejected. The view shows operator
  configuration, which is what the Settings modal already groups (provider, secrets). A separate
  top-level view would fragment the "how is this set up?" surface across two places.
- **Show the iOS runner status alone.** Considered and declined in favor of the comprehensive
  server-configuration view. The runner is the sharpest instance of a general gap: the browser shows
  almost nothing about the running server's configuration. The tab addresses the whole gap and gives
  the runner status its most prominent row.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `GET /api/server` → `ops.server_settings(state)` with hosted-aware redaction.
- [x] Unit 2 — `iosRunner` block (bundled-runner presence, build-info, `xcuitest.testRunner` override).
- [x] Unit 3 — the read-only Server tab in the Settings modal (`serve.html.j2` + `serve.core.mjs`).
- [x] Unit 4 — operation and route-registration tests (local/hosted, bundled/absent, override).

## References

- [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md): bundles the
  generic XCUITest Simulator runner into the wheel. The `_bundled_runner` probe is what this tab
  surfaces.
- [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md): makes
  XCUITest the sole iOS backend, so the runner's presence now decides every iOS Simulator run.
- [BE-0148](../BE-0148-serve-doctor/BE-0148-serve-doctor.md): the complementary per-target doctor
  readiness panel.
- [BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge.md): the running
  commit/version badge, whose value the version row of this tab reuses.
- [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md):
  the hosted-versus-local redaction pattern that the endpoint follows.
