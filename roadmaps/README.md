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
directory lives under one of **four** folders, one per `Status` (BE-0078) — the folder is a
faithful image of how far the item has progressed:

| Status | Folder |
|---|---|
| `Implemented` | `roadmaps/implemented/` |
| `In progress` | `roadmaps/in-progress/` |
| `Proposal` | `roadmaps/proposals/` |
| `Proposal (deferred)` | `roadmaps/deferred/` |

When you add a roadmap item:

1. **Allocate the next ID** = the highest existing `BE-NNNN` + 1, counting items in **all**
   folders. Find it with:
   ```bash
   ls -d roadmaps/{implemented,in-progress,proposals,deferred}/BE-*/ | sort | tail -1
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
   `BE-NNNN-<slug>/BE-NNNN-<slug>.md` (English) and `BE-NNNN-<slug>-ja.md` (Japanese, same ID &
   slug). **Don't hand-edit the index tables below** — they are generated from each item's own
   metadata. Run `make roadmap-index` (or `python scripts/build_roadmap_index.py`) to regenerate the
   tables between the `<!-- GENERATED:* -->` markers in **both** index pages. The item's `Status`
   (its bucket) + `Topic` decide its section, so an item in an existing topic needs no manual table
   edit; the gate (`tests/test_roadmap_index.py`, run by `make test`) fails if the committed index
   drifts. A brand-new topic also needs a marked section plus a `Section` entry in the script.
3. **IDs are permanent.** Never renumber an existing item — not when its status changes, not when
   it is completed, not when it is removed from a table. A BE ID, once assigned, refers to that
   item forever.

Each file follows the **Swift-Evolution proposal format**: a metadata block then `## Introduction`
/ `## Motivation` / `## Detailed design` / `## Alternatives considered` / `## References` (fill
what you can; mark unknowns `TBD`). The metadata is a fenced `| Field | Value |` table —
`<!-- BE-METADATA -->` … `<!-- /BE-METADATA -->`, opening with a `| Field | Value |` header row
(`| 項目 | 値 |` on the Japanese side) and holding `Proposal`, `Author`, `Status`, `Topic` (plus
`Implementing PR` once shipped and `Origin` last, when applicable); the Japanese mirror uses
`提案`, `提案者`, `状態`, `トピック`. **Name the author by GitHub handle** —
`| Author | [@handle](https://github.com/handle) |`, the account of whoever first authored the item
(for an AI-assisted draft, the person who drove and committed it). `tests/test_roadmap_format.py`
checks this shape. **`Status` is the single source of truth**: it decides both the item's folder
(table above) and its index bucket — `Implemented` → Implemented, `In progress` → In progress,
`Proposal` → Proposals, `Proposal (deferred)` → Deferred — so the two can never disagree. When an
item's status changes, set its `Status` and **move its directory** to the folder that status names
(keeping the same ID and slug), then regenerate the index — or run `make roadmap-promote`, which
does the move and the reindex for you.

---

## Implemented

Shipped and in `main` — the project's **implementation record**. Each item's `Status` is
`Implemented` and it lives under `roadmaps/implemented/`.

### Milestones (M1–M4)

Coarse delivery milestones (M1–M4). All four shipped; the finer-grained features they decomposed into live in the topic sections below.

<!-- GENERATED:implemented-milestones -->
| ID | Item | Status |
|---|---|---|
| [BE-0001](implemented/BE-0001-m1-deterministic-runner/BE-0001-m1-deterministic-runner.md) | Deterministic runner (M1) | Implemented |
| [BE-0002](implemented/BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence.md) | AI authoring loop & evidence (M2) | Implemented |
| [BE-0003](implemented/BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md) | codegen, traces, network & CI (M3) | Implemented |
| [BE-0004](implemented/BE-0004-m4-self-healing-triage/BE-0004-m4-self-healing-triage.md) | Self-healing triage (M4) | Implemented |
<!-- /GENERATED:implemented-milestones -->

### Platform expansion (landed slices)

The multi-platform direction, landed slice by slice: the **Web (Playwright) backend** and the **platform-aware backend registry** (so `--backend` / `backend:` accept a platform token — `ios` / `android` / `web` / `fake` — as well as a bare actuator). The rest of each platform's triple is tracked under [Platform expansion](#platform-expansion-android--web--flutter) in Proposals.

<!-- GENERATED:implemented-platform-landed -->
| ID | Item | Status |
|---|---|---|
| [BE-0041](implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) | Web (Playwright) backend | Implemented |
| [BE-0042](implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md) | Platform-aware backend registry & selection | Implemented |
| [BE-0057](implemented/BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets.md) | Rename the config `apps` key to `targets` | Implemented |
| [BE-0066](implemented/BE-0066-web-crawl/BE-0066-web-crawl.md) | Web crawl (Playwright backend) | Implemented |
<!-- /GENERATED:implemented-platform-landed -->

### Authoring experience (record / GUI editor)

The AI-driven `record` (Tier 1) is implemented ([recording.md](../docs/recording.md)). These items make the record → edit → re-run cycle easier for humans — starting with the local web UI launcher `bajutsu serve`.

<!-- GENERATED:implemented-authoring -->
| ID | Item | Status |
|---|---|---|
| [BE-0011](implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) | Local web UI (`bajutsu serve`) | Implemented |
| [BE-0060](implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) | Download / export a run report as a zip | Implemented |
<!-- /GENERATED:implemented-authoring -->

### Self-healing triage (M4)

Lower the maintenance cost of regressions while keeping AI out of the judge role and limited to an investigator.

<!-- GENERATED:implemented-self-healing -->
| ID | Item | Status |
|---|---|---|
| [BE-0021](implemented/BE-0021-ai-triage/BE-0021-ai-triage.md) | AI triage (root-cause summary, fix suggestions) | Implemented |
| [BE-0022](implemented/BE-0022-update-structured-fixes/BE-0022-update-structured-fixes.md) | `update` (minimal-diff proposals = applying structured fixes) | Implemented |
| [BE-0023](implemented/BE-0023-self-healing-guards/BE-0023-self-healing-guards.md) | Guards against "making tests laxer" | Implemented |
<!-- /GENERATED:implemented-self-healing -->

### Candidates from competitive research (MagicPod / Autify)

MagicPod and Autify are built around **AI self-healing + no-code + cloud device farm + visual testing**. Both tools' flagship feature is "AI auto-corrects locators / tap positions during a run", which conflicts directly with Bajutsu's core principle ([DESIGN §2](../DESIGN.md): **keep AI out of the CI gate / determinism first**). Features were evaluated by separating what can be adopted deterministically from what can be adopted only outside the gate.

<!-- GENERATED:implemented-competitive -->
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
| [BE-0039](implemented/BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin.md) | Self-healing limited to "propose + opt-in apply" | Implemented | Both |
<!-- /GENERATED:implemented-competitive -->

### Candidates from competitive research (Maestro)

Determinism-as-contract features sharpened against Maestro's flakiness-tolerance, now landed — starting with behavioral / protocol assertions (event, request sequence, response schema).

<!-- GENERATED:implemented-competitive-maestro -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0048](implemented/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions.md) | Behavioral / protocol assertions | Implemented | Maestro |
<!-- /GENERATED:implemented-competitive-maestro -->

### Integration & automation (MCP)

<!-- GENERATED:implemented-mcp -->
| ID | Item | Status |
|---|---|---|
| [BE-0017](implemented/BE-0017-mcp-server/BE-0017-mcp-server.md) | MCP server | Implemented |
| [BE-0018](implemented/BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources.md) | Return evidence as MCP resources | Implemented |
<!-- /GENERATED:implemented-mcp -->

### Development infrastructure (contributor workflow)

Reduce friction for the many parallel sessions working this repo — treat merge conflicts as a design smell and reshape the file flow so independent changes touch disjoint files.

<!-- GENERATED:implemented-dev-infra -->
| ID | Item | Status |
|---|---|---|
| [BE-0043](implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md) | Conflict-resistant file flow (generated indexes, modular files, git hygiene) | Implemented |
| [BE-0061](implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md) | Collision-proof BE-ID allocation (atomic reservation + auto-repair) | Implemented |
| [BE-0067](implemented/BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md) | Code-quality gate hardening (CI fidelity, security lint, supply-chain) | Implemented |
| [BE-0074](implemented/BE-0074-be-template-standardization/BE-0074-be-template-standardization.md) | Standardize the BE item template (EN / JA) | Implemented |
| [BE-0078](implemented/BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) | Status-driven roadmap folders (proposals / deferred / in-progress / implemented) | Implemented |
<!-- /GENERATED:implemented-dev-infra -->

### Dogfood fixtures (demo apps)

Purpose-built test subjects that exercise the commands end-to-end. The showcase suite is the next-generation dogfood target — the same app written in UIKit and SwiftUI, each in an accessibility-on / accessibility-off variant (four products, two codebases), so `run` (id-based), `record` (no-id fallback), `doctor` (Ready vs Blocked), and `crawl` ([BE-0038](in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)) all have one rich, representative subject. The screen-by-screen contract lives in [`demos/showcase/SPEC.md`](../demos/showcase/SPEC.md).

<!-- GENERATED:implemented-dogfood -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0045](implemented/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md) | Dogfood showcase apps (UIKit × SwiftUI, accessibility-paired) | Implemented | Dogfooding |
<!-- /GENERATED:implemented-dogfood -->

### Dogfood fixtures (web UI)

Bajutsu's own `serve` Web UI is a web app, so the Web (Playwright) backend drives it — a deterministic, Tier-2 regression net for the UI, built on [BE-0041](implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) and the web-side counterpart to the iOS [BE-0045](implemented/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md) showcase fixtures.

<!-- GENERATED:implemented-dogfood-web-ui -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0058](implemented/BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) | Dogfood the serve Web UI (web-backend regression net) | Implemented | Dogfooding |
| [BE-0059](implemented/BE-0059-launch-target-server/BE-0059-launch-target-server.md) | Bring up the target server for a run (`launchServer`) | Implemented | Dogfooding |
<!-- /GENERATED:implemented-dogfood-web-ui -->

### AI provider configuration

The Tier-1 AI paths (`record` / `triage` / `--dismiss-alerts` / `crawl`) call Claude through a
pluggable provider. This topic covers selecting and configuring that provider — e.g. Amazon Bedrock
(authenticated with AWS credentials) as an alternative to the direct Anthropic API. The
deterministic `run` / CI gate calls no model and is unaffected. This axis is distinct from
`backend` (the UI actuator).

<!-- GENERATED:implemented-ai-provider -->
| ID | Item | Status |
|---|---|---|
| [BE-0053](implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md) | Amazon Bedrock as a pluggable AI provider | Implemented |
<!-- /GENERATED:implemented-ai-provider -->

### Hosting the web UI (cloud / self-hosted)

Standing up `bajutsu serve` beyond loopback. The hardening that makes the existing stdlib server
safe to expose (auth, input validation) has shipped; the full hosted topologies (cloud control
plane, self-hosted multi-tenant) remain proposals below.

<!-- GENERATED:implemented-hosting -->
| ID | Item | Status |
|---|---|---|
| [BE-0051](implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) | Serve hardening for hosting (auth, input validation) | Implemented |
<!-- /GENERATED:implemented-hosting -->

### codegen coverage

Turning a passing scenario into a native test in a destination framework's idiom. The web (Playwright) target has landed alongside the original XCUITest one, and the compound-selector / device-control syntax gaps have been shrunk; remaining gaps stay as proposals below.

<!-- GENERATED:implemented-codegen -->
| ID | Item | Status |
|---|---|---|
| [BE-0026](implemented/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) | Shrink unsupported syntax | Implemented |
| [BE-0062](implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md) | Playwright codegen target | Implemented |
<!-- /GENERATED:implemented-codegen -->

### Crawl performance / scale-out

Running the autonomous crawl across more than one device so a full screen map is built in a fraction of the wall-clock time.

<!-- GENERATED:implemented-crawl -->
| ID | Item | Status |
|---|---|---|
| [BE-0064](implemented/BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) | Parallel crawl across multiple simulators | Implemented |
| [BE-0077](implemented/BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md) | Parallel web crawl across multiple browsers | Implemented |
<!-- /GENERATED:implemented-crawl -->

### Miscellaneous

<!-- GENERATED:implemented-misc -->
| ID | Item | Status |
|---|---|---|
| [BE-0028](implemented/BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard.md) | Guard against over-matching evidence rules | Implemented |
<!-- /GENERATED:implemented-misc -->

## In progress

Accepted and being built — a PR is in flight. Each item's `Status` is `In progress` and it lives
under `roadmaps/in-progress/`.

### Platform expansion (landed slices)

Raising the Web (Playwright) backend to the rich end of the capability model — native network, video / console evidence, emulated multi-touch, and parallel runs — on top of the landed v1 slice.

<!-- GENERATED:in-progress-platform-landed -->
| ID | Item | Status |
|---|---|---|
| [BE-0054](in-progress/BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) | Web backend completion (rich capabilities & parallel runs) | In progress |
<!-- /GENERATED:in-progress-platform-landed -->

### Authoring experience (record / GUI editor)

Making the run report a pure rendering of stored run data, so it can be regenerated from a finished run without re-executing the scenario.

<!-- GENERATED:in-progress-authoring -->
| ID | Item | Status |
|---|---|---|
| [BE-0068](in-progress/BE-0068-regenerable-reports/BE-0068-regenerable-reports.md) | Regenerable reports (render from stored run data) | In progress |
<!-- /GENERATED:in-progress-authoring -->

### Candidates from competitive research (MagicPod / Autify)

The MagicPod / Autify candidates currently under construction — autonomous crawl exploration and the next device-state primitives.

<!-- GENERATED:in-progress-competitive -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0038](in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) | Autonomous crawl exploration (App Explorer style) | In progress | Autify VAX |
| [BE-0052](in-progress/BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md) | Device-state primitives: timezone, clipboard, shake | In progress | MagicPod |
<!-- /GENERATED:in-progress-competitive -->

### Candidates from competitive research (Maestro)

Sharpening Bajutsu's determinism-as-contract stance against Maestro's flakiness-tolerance into concrete, machine-checkable features — behavioral assertions, the determinism audit, and the coverage map.

<!-- GENERATED:in-progress-competitive-maestro -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0049](in-progress/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) | Determinism / flakiness audit | In progress | Maestro |
| [BE-0050](in-progress/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) | E2E coverage map | In progress | Maestro |
<!-- /GENERATED:in-progress-competitive-maestro -->

## Proposals

Under consideration — not yet decided. Promote an item to *In progress* when work starts, and to
*Implemented* when it ships.

### On-device validation (M1 close-out)

The deterministic core runs end-to-end on the FakeDriver, and the idb backend's subprocess execution (`describe-all` parsing, frame-center tap/text/swipe) and the simctl launch sequence are validated on a real device (iPhone 17 Pro / latest iOS). What remains is only ongoing maintenance monitoring.

<!-- GENERATED:proposals-on-device -->
| ID | Item | Status |
|---|---|---|
| [BE-0005](proposals/BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md) | idb_companion version monitoring | Proposal |
| [BE-0006](proposals/BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) | idb element-tree normalization accuracy | Proposal |
<!-- /GENERATED:proposals-on-device -->

### Platform expansion (Android / Web / Flutter)

The scope is currently **limited to the iOS Simulator** ([DESIGN §1](../DESIGN.md)). This section covers the direction of going multi-platform by leveraging the driver / backend abstractions — a strategic decision that entails updating the core scope statement. The big-picture overview is in [multi-platform.md](../docs/multi-platform.md); the **concrete per-platform design** lives in the items below: [BE-0009](proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) holds the shared abstractions, then Android and Flutter. Web (recommended first) has already landed ([BE-0041](implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)), as has the platform-aware backend registry ([BE-0042](implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)).

<!-- GENERATED:proposals-platform -->
| ID | Item | Status |
|---|---|---|
| [BE-0007](proposals/BE-0007-android-backend/BE-0007-android-backend.md) | Android backend | Proposal |
| [BE-0008](proposals/BE-0008-flutter-support/BE-0008-flutter-support.md) | Flutter support | Proposal |
| [BE-0009](proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) | Cross-platform abstractions | Proposal |
| [BE-0010](proposals/BE-0010-update-scope-statement/BE-0010-update-scope-statement.md) | Update the scope statement | Proposal |
| [BE-0076](proposals/BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md) | Selectable browser engines & cross-browser compatibility matrix (web backend) | Proposal |
<!-- /GENERATED:proposals-platform -->

### Authoring experience (record / GUI editor)

<!-- GENERATED:proposals-authoring -->
| ID | Item | Status |
|---|---|---|
| [BE-0012](proposals/BE-0012-action-capture-record/BE-0012-action-capture-record.md) | Action-capture record | Proposal |
| [BE-0013](proposals/BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) | Scenario GUI editor | Proposal |
| [BE-0014](proposals/BE-0014-record-demarcation/BE-0014-record-demarcation.md) | Demarcation from the existing AI record | Proposal |
| [BE-0044](proposals/BE-0044-scenario-provenance/BE-0044-scenario-provenance.md) | Scenario provenance (`from:` — step ↔ natural-language origin) | Proposal |
| [BE-0072](proposals/BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md) | Responsive serve Web UI (small-screen & touch layout) | Proposal |
<!-- /GENERATED:proposals-authoring -->

### Hosting the web UI (cloud / self-hosted)

Turn the local `bajutsu serve` launcher into a shared service. The runner drives an iOS Simulator and so needs a Mac, which forces a control-plane (Linux) ⇄ macOS-worker split. [BE-0015](proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) selects a managed, multi-tenant public stack; [BE-0016](proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) covers running it on your own Mac(s) — a today-ready single-Mac path on the existing `serve`, plus a fully self-hosted multi-tenant topology.

<!-- GENERATED:proposals-hosting -->
| ID | Item | Status |
|---|---|---|
| [BE-0015](proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) | Public hosting of the web UI | Proposal |
| [BE-0016](proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) | Self-hosting of the web UI | Proposal |
| [BE-0055](proposals/BE-0055-operational-logging/BE-0055-operational-logging.md) | Operational logging for the hosted serve | Proposal |
| [BE-0070](proposals/BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md) | Live in-progress run artifacts across the worker split | Proposal |
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
| [BE-0073](proposals/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) | Upload a config + scenarios + app-binary bundle as a zip and run it from the web UI | Proposal |
<!-- /GENERATED:proposals-config-sourcing -->

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
<!-- /GENERATED:proposals-codegen -->

### Development infrastructure (contributor workflow)

Lower the cost for the many parallel sessions — human and AI — that read and change this repo.
[BE-0043](implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
(Implemented) reshaped the *file flow*; the proposals here improve how the codebase **documents
itself** to the humans and agents working it.

<!-- GENERATED:proposals-dev-infra -->
| ID | Item | Status |
|---|---|---|
| [BE-0065](proposals/BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md) | Docstring standard & generated API reference | Proposal |
| [BE-0069](proposals/BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md) | Executable contributor guardrails (procedures as commands) | Proposal |
<!-- /GENERATED:proposals-dev-infra -->

### Candidates from competitive research (MagicPod / Autify)

<!-- GENERATED:proposals-competitive -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0037](proposals/BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md) | WebView / hybrid support | Proposal | MagicPod |
| [BE-0046](proposals/BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) | OTP & email side-channel steps | Proposal | MagicPod |
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
<!-- /GENERATED:proposals-competitive-maestro -->

## Deferred

Parked proposals — considered, then deliberately set aside. Each item's `Status` is
`Proposal (deferred)` and it lives under `roadmaps/deferred/`.

### Miscellaneous

<!-- GENERATED:deferred-misc -->
| ID | Item | Status |
|---|---|---|
| [BE-0027](deferred/BE-0027-mock-server-external/BE-0027-mock-server-external.md) | `mockServer` (external mock) | Deferred |
<!-- /GENERATED:deferred-misc -->

### Candidates from competitive research (MagicPod / Autify)

<!-- GENERATED:deferred-competitive -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0040](deferred/BE-0040-ai-assertions/BE-0040-ai-assertions.md) | AI assertions | Deferred | MagicPod |
<!-- /GENERATED:deferred-competitive -->

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
