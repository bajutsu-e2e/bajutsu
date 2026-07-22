**English** · [日本語](README-ja.md)

# Bajutsu roadmap / backlog

> [!IMPORTANT]
> **Ownership of open items lives in GitHub Issues, not in this file.** Every open item (`Status`
> `Proposal` or `In progress`) has a matching GitHub issue, and that issue's **Assignees** are the
> single source of truth for who, if anyone, is working on it — no field in this repo tracks that.
> Browse [issues labeled `roadmap-tracking`](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap-tracking):
> `no:assignee` for the unclaimed backlog, `assignee:<user>` for one person's plate. See
> [BE-0109](BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) for
> details.

> This document tracks features planned for future implementation. Each item has its own file
> (one BE ID per item). Add unformed thoughts to [Unsorted ideas](#unsorted-ideas) first, then
> promote them to a numbered item once the scope is clear.
>
> - **What exists today** is not tracked here. The prose account is
>   [architecture.md#implementation-status](../docs/architecture.md#implementation-status); every
>   shipped item, browsable by topic, lives on the
>   [roadmap dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html) linked from
>   [Implemented](#implemented) below. This page covers what is still open — proposed, in progress,
>   or deferred.
> - The design rationale is in [`DESIGN.md`](../DESIGN.md).
> - **The overall strategic direction** is in [vision.md](../docs/vision.md).

## Adding a roadmap item — BE IDs

Every roadmap item lives under `roadmaps/`. The full procedure — directory layout, ID allocation,
both language files, format — is the single source of truth in
[`docs/ai-development.md`](../docs/ai-development.md#roadmap-items-be-ids-strict).

**Don't hand-edit the index tables below** — they are generated from each item's own metadata; run
`make roadmap-index` to regenerate them. Setting `Status: Implemented` on an item drops its row off
this page entirely rather than moving it to a table here — the dashboard takes over from there.

---

## Implemented

Shipped — landed on `main`. Browse every shipped item, grouped by topic with live progress bars, on
the [roadmap dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html); this page does not
list them individually.

## In progress

Accepted and actively being built — a PR is in flight or imminent.

### Platform support (iOS / Android / Web / Flutter)

<!-- GENERATED:in-progress-platform -->
| ID | Item | Status |
|---|---|---|
| [BE-0292](BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md) | Bundle the XCUITest runner so testRunner is optional | In progress |
<!-- /GENERATED:in-progress-platform -->

### Device-cloud execution

<!-- GENERATED:in-progress-device-cloud -->
| ID | Item | Status |
|---|---|---|
| [BE-0288](BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build.md) | iOS device-signing build for the batch route | In progress |
<!-- /GENERATED:in-progress-device-cloud -->

### Verification & coverage

<!-- GENERATED:in-progress-verification -->
| ID | Item | Status |
|---|---|---|
| [BE-0282](BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) | Real-backend network capture, mock, and assertion coverage in CI | In progress |
| [BE-0285](BE-0285-scenario-feature-real-backend-coverage/BE-0285-scenario-feature-real-backend-coverage.md) | Verify scenario-authoring features on real backends | In progress |
<!-- /GENERATED:in-progress-verification -->

### AI provider configuration

<!-- GENERATED:in-progress-ai-provider -->

<!-- /GENERATED:in-progress-ai-provider -->

### Hosting the web UI (cloud / self-hosted)

<!-- GENERATED:in-progress-hosting -->

<!-- /GENERATED:in-progress-hosting -->

### Codebase quality & technical debt

<!-- GENERATED:in-progress-quality-debt -->

<!-- /GENERATED:in-progress-quality-debt -->

### Authoring experience (record / GUI editor)

<!-- GENERATED:in-progress-authoring -->

<!-- /GENERATED:in-progress-authoring -->

## Proposals

Under consideration — not yet decided. Promote an item to *In progress* once work starts, or to *Implemented* when it ships.

### Platform support (iOS / Android / Web / Flutter)

<!-- GENERATED:proposals-platform -->
| ID | Item | Status |
|---|---|---|
| [BE-0008](BE-0008-flutter-support/BE-0008-flutter-support.md) | Flutter support | Proposal |
| [BE-0289](BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md) | Make the XCUITest channel re-resolve a stale actuation handle before failing | Proposal |
| [BE-0290](BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md) | Make XCUITest the default iOS backend and retire idb | Proposal |
<!-- /GENERATED:proposals-platform -->

### Driver & backend architecture

<!-- GENERATED:proposals-driver-architecture -->

<!-- /GENERATED:proposals-driver-architecture -->

### Device-cloud execution

Running a scenario on a hosted device farm instead of a local Simulator, emulator, or browser, behind a common provider abstraction — an opt-in execution target beyond the deterministic core's local-first default.

<!-- GENERATED:proposals-device-cloud -->
| ID | Item | Status |
|---|---|---|
| [BE-0237](BE-0237-firebase-device-streaming-adapter/BE-0237-firebase-device-streaming-adapter.md) | Firebase Test Lab / Device Streaming adapter | Proposal |
<!-- /GENERATED:proposals-device-cloud -->

### Scenario authoring features

<!-- GENERATED:proposals-scenario-authoring -->

<!-- /GENERATED:proposals-scenario-authoring -->

### Verification & coverage

<!-- GENERATED:proposals-verification -->

<!-- /GENERATED:proposals-verification -->

### Authoring experience (record / GUI editor)

<!-- GENERATED:proposals-authoring -->
| ID | Item | Status |
|---|---|---|
| [BE-0295](BE-0295-record-crawl-real-model-verification/BE-0295-record-crawl-real-model-verification.md) | Real-model verification of the record and crawl propose loops | Proposal |
<!-- /GENERATED:proposals-authoring -->

### codegen coverage

<!-- GENERATED:proposals-codegen -->
| ID | Item | Status |
|---|---|---|
| [BE-0293](BE-0293-codegen-playwright-real-compile/BE-0293-codegen-playwright-real-compile.md) | Real-compile verification for the Playwright (TypeScript) codegen target | Proposal |
| [BE-0294](BE-0294-codegen-uiautomator-real-compile/BE-0294-codegen-uiautomator-real-compile.md) | Real-compile verification for the UI Automator (Kotlin) codegen target | Proposal |
| [BE-0297](BE-0297-codegen-xcuitest-dsl-coverage/BE-0297-codegen-xcuitest-dsl-coverage.md) | Expand XCUITest codegen's real-compile coverage to the full DSL surface | Proposal |
<!-- /GENERATED:proposals-codegen -->

### Self-healing triage

<!-- GENERATED:proposals-self-healing -->
| ID | Item | Status |
|---|---|---|
| [BE-0296](BE-0296-triage-ai-real-model-verification/BE-0296-triage-ai-real-model-verification.md) | Real-model verification of the triage --ai diagnosis path | Proposal |
<!-- /GENERATED:proposals-self-healing -->

### Surfacing CLI features in the serve Web UI

<!-- GENERATED:proposals-serve-cli-features -->

<!-- /GENERATED:proposals-serve-cli-features -->

### Hosting the web UI (cloud / self-hosted)

<!-- GENERATED:proposals-hosting -->
| ID | Item | Status |
|---|---|---|
| [BE-0167](BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md) | Control-plane scale-out behind a load balancer | Proposal |
| [BE-0168](BE-0168-self-host-high-availability/BE-0168-self-host-high-availability.md) | Self-hosted high availability and single-point-of-failure hardening | Proposal |
| [BE-0170](BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch.md) | Weighted-fair cross-org job dispatch | Proposal |
| [BE-0244](BE-0244-deploy-hosted-web-ui-service/BE-0244-deploy-hosted-web-ui-service.md) | Deploy the hosted web UI service | Proposal |
<!-- /GENERATED:proposals-hosting -->

### Configuration sourcing

<!-- GENERATED:proposals-config-sourcing -->

<!-- /GENERATED:proposals-config-sourcing -->

### Security hardening

<!-- GENERATED:proposals-security -->

<!-- /GENERATED:proposals-security -->

### Development infrastructure (contributor workflow)

<!-- GENERATED:proposals-developer-experience -->

<!-- /GENERATED:proposals-developer-experience -->

### Codebase quality & technical debt

<!-- GENERATED:proposals-quality-debt -->

<!-- /GENERATED:proposals-quality-debt -->

## Deferred

Parked proposals — considered, then shelved for now. Kept here (not deleted) so the decision and its rationale stay on record; un-defer by changing `Status` back to `Proposal`.

### Scenario authoring features

<!-- GENERATED:deferred-scenario-authoring -->
| ID | Item | Status |
|---|---|---|
| [BE-0157](BE-0157-shake-device-primitive/BE-0157-shake-device-primitive.md) | Shake device primitive | Deferred |
| [BE-0158](BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive.md) | Timezone device primitive | Deferred |
<!-- /GENERATED:deferred-scenario-authoring -->

### Verification & coverage

<!-- GENERATED:deferred-verification -->
| ID | Item | Status |
|---|---|---|
| [BE-0040](BE-0040-ai-assertions/BE-0040-ai-assertions.md) | AI assertions | Deferred |
<!-- /GENERATED:deferred-verification -->

### Hosting the web UI (cloud / self-hosted)

<!-- GENERATED:deferred-hosting -->
| ID | Item | Status |
|---|---|---|
| [BE-0070](BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md) | Live in-progress run artifacts across the worker split | Deferred |
<!-- /GENERATED:deferred-hosting -->

### Security hardening

<!-- GENERATED:deferred-security -->
| ID | Item | Status |
|---|---|---|
| [BE-0154](BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha.md) | Run roadmap-promote from the base SHA | Deferred |
<!-- /GENERATED:deferred-security -->

### Miscellaneous / on hold

<!-- GENERATED:deferred-misc -->
| ID | Item | Status |
|---|---|---|
| [BE-0027](BE-0027-mock-server-external/BE-0027-mock-server-external.md) | `mockServer` (external mock) | Deferred |
<!-- /GENERATED:deferred-misc -->

## Not adopting (already covered / out of scope)

- **Change history / version management** — already covered, since scenarios are YAML under git.
- **Cloud device farm / real-device execution as the *default*** — the deterministic core stays local-first and CI-friendly (Simulator, headless browser, emulator), not real hardware or device clouds ([DESIGN §1](../DESIGN.md)). Hosted device-cloud execution is not the default, but it is no longer flatly out of scope: it is tracked as opt-in proposals under *Device-cloud execution*. Multi-platform likewise lives under the *Platform support* items.
- **Per-step screenshots / UI tree on error / device logs** — already covered by the evidence subsystem (capturePolicy + the `result:error` safety net).
- **NL→test generation (Autopilot equivalent)** — overlaps with the existing `record` + the *Authoring experience* items.
- **Scheduling / Slack / TestRail integration** — the domain of the CI / notification layer. Low priority (separately, if needed).
- **Automatic retry of failed tests** — in tension with determinism-first (no fixed sleeps, condition waits). It can hide flakiness, so if adopted at all it should be limited to quarantine use and needs careful consideration.

---

## Unsorted ideas

> Add unformed thoughts here. Promote them to a numbered BE item later.

-
