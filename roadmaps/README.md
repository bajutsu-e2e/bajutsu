**English** · [日本語](README-ja.md)

# Bajutsu roadmap / backlog

> This document tracks features planned for future implementation. Each item has its own file
> (one BE ID per item). Add unformed thoughts to [Unsorted ideas](#unsorted-ideas) first, then
> promote them to a numbered item once the scope is clear.
>
> - **The accurate list of what exists today (implemented / unwired)** is in
>   [architecture.md#implementation-status](../docs/architecture.md#implementation-status) — the source
>   of truth. This page covers what is planned next.
> - The design rationale is in [`DESIGN.md`](../DESIGN.md).
> - **The overall strategic direction** is in [vision.md](../docs/vision.md).

## Legend

**Priority** — `P0` (do next) / `P1` (will do) / `P2` (nice to have) / `P3` (idea stage)
**Status** — 💡 idea / 📋 planned / 🚧 in progress / ❄️ on hold / ✅ done

## Adding a roadmap item — BE IDs (agents MUST follow)

Every roadmap item is a directory `BE-NNNN-<slug>/` holding the English file `BE-NNNN-<slug>.md`
and its Japanese version `BE-NNNN-<slug>-ja.md` (same ID and slug). **BE** stands for *Bajutsu
Evolution* and `NNNN` is a **zero-padded, 4-digit, monotonically increasing** ID. Each item
directory lives under one of two folders by how far it has progressed:
`roadmaps/implemented/` for shipped items (`Status: Implemented`) and `roadmaps/proposals/`
for everything still in flight.

When you add a roadmap item:

1. **Allocate the next ID** = the highest existing `BE-NNNN` + 1, counting items in **both**
   folders. Find it with:
   ```bash
   ls -d roadmaps/{implemented,proposals}/BE-*/ | sort | tail -1
   ```
   Never reuse, skip, or guess a number. **Or leave it undetermined:** name the item
   `BE-XXXX-<slug>` (the literal placeholder) and let CI assign the number — the
   [`roadmap-id`](../.github/workflows/roadmap-id.yml) workflow runs
   [`scripts/allocate_roadmap_ids.py`](../scripts/allocate_roadmap_ids.py) on every PR
   touching `roadmaps/**`, allocates the next free IDs, and pushes the rename back to
   the branch. This is what the `ideation` skill does, and it avoids two
   in-flight branches racing for the same number.
2. **Create the item directory and both language files** — under `roadmaps/proposals/` for a
   proposal, or under `roadmaps/implemented/` with `Status: Implemented` when the same PR also ships
   the implementation (a new item is a proposal first *unless* its code lands with it) —
   `roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md` (English) and
   `roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md` (Japanese, same ID & slug). **Don't hand-edit the
   index tables below** — they are generated from each item's own metadata. Run `make roadmap-index`
   (or `python scripts/build_roadmap_index.py`) to regenerate the tables between the `<!-- GENERATED:* -->`
   markers in **both** index pages. The item's `Track` + `Topic` decide its section, so an item in an
   existing topic needs no manual table edit; the gate (`tests/test_roadmap_index.py`, run by `make test`)
   fails if the committed index drifts. A brand-new topic also needs a marked section plus a `Section`
   entry in the script.
3. **IDs are permanent.** Never renumber an existing item — not when its status changes, not when
   it is completed, not when it is removed from a table. A BE ID, once assigned, refers to that
   item forever.

Each file follows the **Swift-Evolution proposal format**: a metadata block (`* Proposal`,
`* Author`, `* Status`, `* Track`, `* Topic`, …) then `## Introduction` / `## Motivation` /
`## Detailed design` / `## Alternatives considered` / `## References` (fill what you can; mark
unknowns `TBD`). **Name the author by GitHub handle** —
`* Author: [@handle](https://github.com/handle)`, the account of whoever first authored the item
(for an AI-assisted draft, the person who drove and committed it). The **Status** decides the
track: `Implemented` or `Accepted, in progress` are
listed under **Accepted** (a decision/implementation record); `Proposal` or `Proposal (deferred)`
under **Proposals** (under consideration). When an item ships, set its Status to `Implemented`
and **move its directory** from `roadmaps/proposals/` to `roadmaps/implemented/` (keeping the
same ID and slug), then regenerate the index.

---

## Accepted

Decisions that have been made — already implemented, or accepted and to be implemented. This is the project's **decision & implementation record**.

### Milestones (M1–M4)

Coarse delivery milestones (M1–M4) — the project's **decision & implementation record**. All four are accepted and shipped; the finer-grained features they decomposed into live in the topic sections below.

<!-- GENERATED:accepted-milestones -->
| ID | Item | Status |
|---|---|---|
| [BE-0001](implemented/BE-0001-m1-deterministic-runner/BE-0001-m1-deterministic-runner.md) | Deterministic runner (M1) | Implemented |
| [BE-0002](implemented/BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence.md) | AI authoring loop & evidence (M2) | Implemented |
| [BE-0003](implemented/BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md) | codegen, traces, network & CI (M3) | Implemented |
| [BE-0004](implemented/BE-0004-m4-self-healing-triage/BE-0004-m4-self-healing-triage.md) | Self-healing triage (M4) | Implemented |
<!-- /GENERATED:accepted-milestones -->

### Platform expansion (landed slices)

The first slice of the multi-platform direction has landed: a **platform-aware backend registry** so `--backend` / `backend:` accept a platform token (`ios` / `android` / `web` / `fake`) as well as a bare actuator, expanding to the first implemented-and-available actuator. The rest of each platform's triple (per-platform environment manager + actuator driver) is tracked under [Platform expansion](#platform-expansion-android--web--flutter) in Proposals.

<!-- GENERATED:accepted-platform-landed -->
| ID | Item | Status |
|---|---|---|
| [BE-0041](proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) | Web (Playwright) backend | In progress |
| [BE-0042](implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md) | Platform-aware backend registry & selection | Implemented |
| [BE-0066](implemented/BE-0066-web-crawl/BE-0066-web-crawl.md) | Web crawl (Playwright backend) | Implemented |
<!-- /GENERATED:accepted-platform-landed -->

### Authoring experience (record / GUI editor)

The AI-driven `record` (Tier 1) is implemented ([recording.md](../docs/recording.md)). The items here cover **non-AI action capture** and **visual editing of scenarios**, to make the record → edit → re-run cycle easier for humans. The local web UI launcher `bajutsu serve` is the first step toward this.

<!-- GENERATED:accepted-authoring -->
| ID | Item | Status |
|---|---|---|
| [BE-0011](implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) | Local web UI (`bajutsu serve`) | Implemented |
<!-- /GENERATED:accepted-authoring -->

### Self-healing triage (M4)

Lower the maintenance cost of regressions while keeping AI out of the judge role and limited to an investigator.

<!-- GENERATED:accepted-self-healing -->
| ID | Item | Status |
|---|---|---|
| [BE-0021](implemented/BE-0021-ai-triage/BE-0021-ai-triage.md) | AI triage (root-cause summary, fix suggestions) | Implemented |
| [BE-0022](implemented/BE-0022-update-structured-fixes/BE-0022-update-structured-fixes.md) | `update` (minimal-diff proposals = applying structured fixes) | Implemented |
| [BE-0023](implemented/BE-0023-self-healing-guards/BE-0023-self-healing-guards.md) | Guards against "making tests laxer" | Implemented |
<!-- /GENERATED:accepted-self-healing -->

### Candidates from competitive research (MagicPod / Autify)

MagicPod and Autify are built around **AI self-healing + no-code + cloud device farm + visual testing**. Both tools' flagship feature is "AI auto-corrects locators / tap positions during a run", which conflicts directly with Bajutsu's core principle ([DESIGN §2](../DESIGN.md): **keep AI out of the CI gate / determinism first**). Features were evaluated by separating what can be adopted deterministically from what can be adopted only outside the gate.

<!-- GENERATED:accepted-competitive -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0029](implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md) | Visual-regression assertions | Implemented | Both |
| [BE-0030](implemented/BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md) | Parameterized shared steps | Implemented | MagicPod |
| [BE-0031](implemented/BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md) | Data-driven scenarios | Implemented | MagicPod |
| [BE-0032](implemented/BE-0032-secret-variables/BE-0032-secret-variables.md) | Secret variables | Implemented | MagicPod |
| [BE-0033](implemented/BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md) | Scenario variables + light control flow | Implemented | MagicPod |
| [BE-0034](implemented/BE-0034-tags-selective-runs/BE-0034-tags-selective-runs.md) | Tags / labels + selective runs | Implemented | MagicPod |
| [BE-0035](implemented/BE-0035-device-control-primitives/BE-0035-device-control-primitives.md) | Device-control steps (background, status-bar override) | Implemented | MagicPod |
| [BE-0036](implemented/BE-0036-utility-steps/BE-0036-utility-steps.md) | HTTP utility step | Implemented | MagicPod |
| [BE-0038](proposals/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) | Autonomous crawl exploration (App Explorer style) | In progress | Autify VAX |
| [BE-0039](implemented/BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin.md) | Self-healing limited to "propose + opt-in apply" | Implemented | Both |
<!-- /GENERATED:accepted-competitive -->

### Integration & automation (MCP)

<!-- GENERATED:accepted-mcp -->
| ID | Item | Status |
|---|---|---|
| [BE-0017](implemented/BE-0017-mcp-server/BE-0017-mcp-server.md) | MCP server | Implemented |
| [BE-0018](implemented/BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources.md) | Return evidence as MCP resources | Implemented |
<!-- /GENERATED:accepted-mcp -->

### Development infrastructure (contributor workflow)

Reduce friction for the many parallel sessions working this repo — treat merge conflicts as a design smell and reshape the file flow so independent changes touch disjoint files.

<!-- GENERATED:accepted-dev-infra -->
| ID | Item | Status |
|---|---|---|
| [BE-0043](implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md) | Conflict-resistant file flow (generated indexes, modular files, git hygiene) | Implemented |
| [BE-0061](implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md) | Collision-proof BE-ID allocation (atomic reservation + auto-repair) | Implemented |
| [BE-0067](implemented/BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md) | Code-quality gate hardening (CI fidelity, security lint, supply-chain) | Implemented |
<!-- /GENERATED:accepted-dev-infra -->

### Dogfood fixtures (demo apps)

Purpose-built test subjects that exercise the commands end-to-end. The showcase suite is the next-generation dogfood target — the same app written in UIKit and SwiftUI, each in an accessibility-on / accessibility-off variant (four products, two codebases), so `run` (id-based), `record` (no-id fallback), `doctor` (Ready vs Blocked), and the forthcoming `crawl` ([BE-0038](proposals/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)) all have one rich, representative subject. The screen-by-screen contract lives in [`demos/showcase/SPEC.md`](../demos/showcase/SPEC.md).

<!-- GENERATED:accepted-dogfood -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0045](implemented/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md) | Dogfood showcase apps (UIKit × SwiftUI, accessibility-paired) | Implemented | Dogfooding |
<!-- /GENERATED:accepted-dogfood -->

### Dogfood fixtures (web UI)

Bajutsu's own `serve` Web UI is a web app, so the Web (Playwright) backend drives it — a deterministic, Tier-2 regression net for the UI, built on [BE-0041](proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) and the web-side counterpart to the iOS [BE-0045](implemented/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md) showcase fixtures.

<!-- GENERATED:accepted-dogfood-web-ui -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0058](implemented/BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) | Dogfood the serve Web UI (web-backend regression net) | Implemented | Dogfooding |
| [BE-0059](implemented/BE-0059-launch-target-server/BE-0059-launch-target-server.md) | Bring up the target server for a run (`launchServer`) | Implemented | Dogfooding |
<!-- /GENERATED:accepted-dogfood-web-ui -->

### AI provider configuration

The Tier-1 AI paths (`record` / `triage` / `--dismiss-alerts` / `crawl`) call Claude through a
pluggable provider. This topic covers selecting and configuring that provider — e.g. Amazon Bedrock
(authenticated with AWS credentials) as an alternative to the direct Anthropic API. The
deterministic `run` / CI gate calls no model and is unaffected. This axis is distinct from
`backend` (the UI actuator).

<!-- GENERATED:accepted-ai-provider -->
| ID | Item | Status |
|---|---|---|
| [BE-0053](implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md) | Amazon Bedrock as a pluggable AI provider | Implemented |
<!-- /GENERATED:accepted-ai-provider -->

### Hosting the web UI (cloud / self-hosted)

Standing up `bajutsu serve` beyond loopback. The hardening that makes the existing stdlib server
safe to expose (auth, input validation) has shipped; the full hosted topologies (cloud control
plane, self-hosted multi-tenant) remain proposals below.

<!-- GENERATED:accepted-hosting -->
| ID | Item | Status |
|---|---|---|
| [BE-0051](implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) | Serve hardening for hosting (auth, input validation) | Implemented |
<!-- /GENERATED:accepted-hosting -->

### codegen coverage

Turning a passing scenario into a native test in a destination framework's idiom. The web (Playwright) target has landed alongside the original XCUITest one; remaining gaps stay as proposals below.

<!-- GENERATED:accepted-codegen -->
| ID | Item | Status |
|---|---|---|
| [BE-0062](implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md) | Playwright codegen target | Implemented |
<!-- /GENERATED:accepted-codegen -->

## Proposals

Under consideration — not yet decided. Promote an item to *Accepted* once a decision is made.

### On-device validation (M1 close-out)

The deterministic core runs end-to-end on the FakeDriver, and the idb backend's subprocess execution (`describe-all` parsing, frame-center tap/text/swipe) and the simctl launch sequence are validated on a real device (iPhone 17 Pro / latest iOS). What remains is only ongoing maintenance monitoring.

<!-- GENERATED:proposals-on-device -->
| ID | Item | Status |
|---|---|---|
| [BE-0005](proposals/BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md) | idb_companion version monitoring | Proposal |
| [BE-0006](proposals/BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) | idb element-tree normalization accuracy | Proposal |
<!-- /GENERATED:proposals-on-device -->

### Platform expansion (Android / Web / Flutter)

The scope is currently **limited to the iOS Simulator** ([DESIGN §1](../DESIGN.md)). This section covers the direction of going multi-platform by leveraging the driver / backend abstractions — a strategic decision that entails updating the core scope statement. The big-picture overview is in [multi-platform.md](../docs/multi-platform.md); the **concrete per-platform design** lives in the items below: [BE-0009](proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) holds the shared abstractions, then Web (recommended first, runs on the existing Linux gate), Android, and Flutter. The first slice — a platform-aware backend registry — has already landed ([BE-0042](implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md), under Accepted).

<!-- GENERATED:proposals-platform -->
| ID | Item | Status |
|---|---|---|
| [BE-0007](proposals/BE-0007-android-backend/BE-0007-android-backend.md) | Android backend | Proposal |
| [BE-0008](proposals/BE-0008-flutter-support/BE-0008-flutter-support.md) | Flutter support | Proposal |
| [BE-0009](proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) | Cross-platform abstractions | Proposal |
| [BE-0010](proposals/BE-0010-update-scope-statement/BE-0010-update-scope-statement.md) | Update the scope statement | Proposal |
| [BE-0054](proposals/BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) | Web backend completion (rich capabilities & parallel runs) | Proposal |
| [BE-0057](proposals/BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets.md) | Rename the config `apps` key to `targets` | Proposal |
<!-- /GENERATED:proposals-platform -->

### Authoring experience (record / GUI editor)

<!-- GENERATED:proposals-authoring -->
| ID | Item | Status |
|---|---|---|
| [BE-0012](proposals/BE-0012-action-capture-record/BE-0012-action-capture-record.md) | Action-capture record | Proposal |
| [BE-0013](proposals/BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) | Scenario GUI editor | Proposal |
| [BE-0014](proposals/BE-0014-record-demarcation/BE-0014-record-demarcation.md) | Demarcation from the existing AI record | Proposal |
| [BE-0044](proposals/BE-0044-scenario-provenance/BE-0044-scenario-provenance.md) | Scenario provenance (`from:` — step ↔ natural-language origin) | Proposal |
| [BE-0060](proposals/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) | Download / export a run report as a zip | Proposal |
| [BE-0068](proposals/BE-0068-regenerable-reports/BE-0068-regenerable-reports.md) | Regenerable reports (render from stored run data) | Proposal |
<!-- /GENERATED:proposals-authoring -->

### Hosting the web UI (cloud / self-hosted)

Turn the local `bajutsu serve` launcher into a shared service. The runner drives an iOS Simulator and so needs a Mac, which forces a control-plane (Linux) ⇄ macOS-worker split. [BE-0015](proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) selects a managed, multi-tenant public stack; [BE-0016](proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) covers running it on your own Mac(s) — a today-ready single-Mac path on the existing `serve`, plus a fully self-hosted multi-tenant topology.

<!-- GENERATED:proposals-hosting -->
| ID | Item | Status |
|---|---|---|
| [BE-0015](proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) | Public hosting of the web UI | Proposal |
| [BE-0016](proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) | Self-hosting of the web UI | Proposal |
| [BE-0055](proposals/BE-0055-operational-logging/BE-0055-operational-logging.md) | Operational logging for the hosted serve | Proposal |
<!-- /GENERATED:proposals-hosting -->

### Configuration sourcing

Where `bajutsu` reads its config and scenario tree from. Today that is a local path; the item here
proposes naming a **Git repository at a ref** (`github:owner/repo@ref:path`) so a hosted or
self-hosted `serve`, or a CI runner, can pull a team's test repo directly — scenarios are already
git-tracked files ([DESIGN §6.5](../DESIGN.md)).

<!-- GENERATED:proposals-config-sourcing -->
| ID | Item | Status |
|---|---|---|
| [BE-0063](proposals/BE-0063-git-config-source/BE-0063-git-config-source.md) | Load config (and its scenario tree) from a Git repository + ref | Proposal |
<!-- /GENERATED:proposals-config-sourcing -->

### Integration & automation (MCP)

<!-- GENERATED:proposals-mcp -->
| ID | Item | Status |
|---|---|---|
<!-- /GENERATED:proposals-mcp -->

### Backend expansion (iOS actuators)

<!-- GENERATED:proposals-backend -->
| ID | Item | Status |
|---|---|---|
| [BE-0019](proposals/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) | XCUITest backend | Proposal |
| [BE-0020](proposals/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md) | Multi-backend evidence fallback | Proposal |
<!-- /GENERATED:proposals-backend -->

### doctor / onboarding

<!-- GENERATED:proposals-doctor -->
| ID | Item | Status |
|---|---|---|
| [BE-0024](proposals/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md) | doctor / onboarding | Proposal |
<!-- /GENERATED:proposals-doctor -->

### codegen coverage

<!-- GENERATED:proposals-codegen -->
| ID | Item | Status |
|---|---|---|
| [BE-0025](proposals/BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation.md) | Coordinate swipe generation | Proposal |
| [BE-0026](proposals/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) | Shrink unsupported syntax | Proposal |
<!-- /GENERATED:proposals-codegen -->

### Crawl performance / scale-out

<!-- GENERATED:proposals-crawl -->
| ID | Item | Status |
|---|---|---|
| [BE-0064](proposals/BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) | Parallel crawl across multiple simulators | Proposal |
<!-- /GENERATED:proposals-crawl -->

### Development infrastructure (contributor workflow)

Lower the cost for the many parallel sessions — human and AI — that read and change this repo.
[BE-0043](implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
(Accepted) reshaped the *file flow*; the proposals here improve how the codebase **documents
itself** to the humans and agents working it.

<!-- GENERATED:proposals-dev-infra -->
| ID | Item | Status |
|---|---|---|
| [BE-0065](proposals/BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md) | Docstring standard & generated API reference | Proposal |
| [BE-0069](proposals/BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md) | Executable contributor guardrails (procedures as commands) | Proposal |
<!-- /GENERATED:proposals-dev-infra -->

### Miscellaneous / on hold

<!-- GENERATED:proposals-misc -->
| ID | Item | Status |
|---|---|---|
| [BE-0027](proposals/BE-0027-mock-server-external/BE-0027-mock-server-external.md) | `mockServer` (external mock) | Deferred |
| [BE-0028](proposals/BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard.md) | Guard against over-matching evidence rules | Proposal |
<!-- /GENERATED:proposals-misc -->

### Candidates from competitive research (MagicPod / Autify)

<!-- GENERATED:proposals-competitive -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0037](proposals/BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md) | WebView / hybrid support | Proposal | MagicPod |
| [BE-0040](proposals/BE-0040-ai-assertions/BE-0040-ai-assertions.md) | AI assertions | Deferred | MagicPod |
| [BE-0046](proposals/BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) | OTP & email side-channel steps | Proposal | MagicPod |
| [BE-0052](proposals/BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md) | Device-state primitives: timezone, clipboard, shake | Proposal | MagicPod |
<!-- /GENERATED:proposals-competitive -->

### Candidates from competitive research (Maestro)

Maestro (mobile.dev) is an open-source, cross-platform UI E2E tool whose 2026 direction leans into
breadth, a hosted device cloud (Robin), and AI features that are *optional / advisory by default*
and routed through a vendor-managed cloud. These items sharpen Bajutsu's opposite stance —
determinism as a contract, verification below the UI, and AI strictly under the user's control —
into concrete features that a UI-layer, no-instrumentation competitor cannot easily match.

<!-- GENERATED:proposals-competitive-maestro -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0047](proposals/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) | AI data sovereignty (provider-agnostic, redacted AI path) | Proposal | Maestro |
| [BE-0048](proposals/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions.md) | Behavioral / protocol assertions | Proposal | Maestro |
| [BE-0049](proposals/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) | Determinism / flakiness audit | Proposal | Maestro |
| [BE-0050](proposals/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) | E2E coverage map | Proposal | Maestro |
<!-- /GENERATED:proposals-competitive-maestro -->

## Not adopting (already covered / out of scope)

- **Change history / version management** — already covered, since scenarios are YAML under git.
- **Cloud device farm / real-device / cloud execution** — out of the current iOS-Simulator-only scope ([DESIGN §1](../DESIGN.md)). Multi-platform is tracked as proposals (the *Platform expansion* items).
- **Per-step screenshots / UI tree on error / device logs** — already covered by the evidence subsystem (capturePolicy + the `result:error` safety net).
- **NL→test generation (Autopilot equivalent)** — overlaps with the existing `record` + the *Authoring experience* items.
- **Scheduling / Slack / TestRail integration** — the domain of the CI / notification layer. Low priority (separately, if needed).
- **Automatic retry of failed tests** — in tension with determinism-first (no fixed sleeps, condition waits). It can hide flakiness, so if adopted at all it should be limited to quarantine use and needs careful consideration.

---

## Unsorted ideas

> Add unformed thoughts here. Promote them to a numbered BE item later.

-
