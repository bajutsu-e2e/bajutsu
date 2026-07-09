**English** · [日本語](README-ja.md)

# Bajutsu roadmap / backlog

> [!IMPORTANT]
> **Ownership of open items lives in GitHub Issues, not in this file.** Every open item (`Status`
> `Proposal` or `In progress`) has a matching GitHub issue, and that issue's **Assignees** are the
> single source of truth for who, if anyone, is working on it — no field in this repo tracks that.
> Browse [issues labeled `roadmap-tracking`](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap-tracking):
> `no:assignee` for the unclaimed backlog, `assignee:<user>` for one person's plate. See
> [BE-0109](implemented/BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) for
> details.

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
directory lives under one of **four** folders, one per `Status` value (BE-0078) —
`roadmaps/implemented/` (`Implemented`), `roadmaps/in-progress/` (`In progress`),
`roadmaps/proposals/` (`Proposal`), `roadmaps/deferred/` (`Proposal (deferred)`). `Status` is the
single source of truth: it decides both the folder an item lives in and the index bucket it lists
under, so the two can never disagree.

When you add a roadmap item:

1. **Allocate the next ID** = the highest existing `BE-NNNN` + 1, counting items in **all four**
   folders. Find it with:
   ```bash
   ls -d roadmaps/{implemented,in-progress,proposals,deferred}/BE-*/ | sort | tail -1
   ```
   Never reuse, skip, or guess a number. **The norm, though, is to leave it undetermined:** name the
   item `BE-XXXX-<slug>` (the literal placeholder) and let CI assign the number. The item keeps
   `BE-XXXX` through review and the merge, and the [`roadmap-id`](../.github/workflows/roadmap-id.yml)
   workflow runs [`scripts/allocate_roadmap_ids.py`](../scripts/allocate_roadmap_ids.py) **on `main`
   after the PR merges**, allocating the next free IDs in merge order and committing the rename to
   `main` (BE-0089). This is what the `ideation` skill does; it keeps the `BE-NNNN` sequence
   contiguous (a rejected PR never spends a number) and avoids two in-flight branches racing for one.
   A BE-creation PR therefore carries **no `[BE-NNNN]` title prefix** — the real number is not known
   until after the merge.
2. **Create the item directory and both language files** — under `roadmaps/proposals/` for a
   proposal, or under `roadmaps/implemented/` with `Status: Implemented` when the same PR also ships
   the implementation (a new item is a proposal first *unless* its code lands with it) —
   `roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md` (English) and
   `roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md` (Japanese, same ID & slug). **Don't hand-edit the
   index tables below** — they are generated from each item's own metadata. Run `make roadmap-index`
   (or `python scripts/build_roadmap_index.py`) to regenerate the tables between the `<!-- GENERATED:* -->`
   markers in **both** index pages. The item's `Status` (its bucket) + `Topic` decide its section, so an
   item in an existing section needs no manual table edit; the gate (`tests/test_roadmap_index.py`, run by
   `make test`) fails if the committed index drifts. The first item of a topic to reach a bucket needs a
   new marked section in the page (the generator names the missing region).
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
checks this shape. The **Status** decides the folder and the index bucket: `Implemented` /
`In progress` / `Proposal` / `Proposal (deferred)`. When an item's status changes — it starts being
built, or it ships — set its `Status` and **move its directory** to the matching folder (keeping the
same ID and slug), then regenerate the index. `make roadmap-promote` reconciles any misfiled item
for you.

Write the Japanese file (`*-ja.md`) in **敬体 (the polite *desu/masu* style, ですます調)**,
consistent with `docs/ja/` — never the plain *da/dearu* style (常体). This is part of the
[`japanese-tech-writing`](../.claude/skills/japanese-tech-writing/) norm; a translation must read
as natural polite Japanese, not a literal rendering of the English.

---

## Implemented

Shipped — landed on `main`. This is the project's **implementation record**.

### Milestones (M1–M4)

Coarse delivery milestones (M1–M4). All four are shipped; the finer-grained features they decomposed into live in the topic sections below.

<!-- GENERATED:implemented-milestones -->
| ID | Item | Status |
|---|---|---|
| [BE-0001](BE-0001-m1-deterministic-runner/BE-0001-m1-deterministic-runner.md) | Deterministic runner (M1) | Implemented |
| [BE-0002](BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence.md) | AI authoring loop & evidence (M2) | Implemented |
| [BE-0003](BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md) | codegen, traces, network & CI (M3) | Implemented |
| [BE-0004](BE-0004-m4-self-healing-triage/BE-0004-m4-self-healing-triage.md) | Self-healing triage (M4) | Implemented |
<!-- /GENERATED:implemented-milestones -->

### Platform expansion (landed slices)

The first slices of the multi-platform direction that have shipped: a **platform-aware backend registry** so `--backend` / `backend:` accept a platform token (`ios` / `android` / `web` / `fake`) as well as a bare actuator, plus the config `apps`→`targets` rename and web crawl. The slices still being built are under [In progress](#platform-expansion-landed-slices-1); the rest of each platform's triple is under [Proposals](#platform-expansion-android--web--flutter).

<!-- GENERATED:implemented-platform-landed -->
| ID | Item | Status |
|---|---|---|
| [BE-0041](BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) | Web (Playwright) backend | Implemented |
| [BE-0042](BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md) | Platform-aware backend registry & selection | Implemented |
| [BE-0054](BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) | Web backend completion (rich capabilities & parallel runs) | Implemented |
| [BE-0057](BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets.md) | Rename the config `apps` key to `targets` | Implemented |
| [BE-0066](BE-0066-web-crawl/BE-0066-web-crawl.md) | Web crawl (Playwright backend) | Implemented |
<!-- /GENERATED:implemented-platform-landed -->

### Platform expansion (Android / Web / Flutter)

<!-- GENERATED:implemented-platform -->
| ID | Item | Status |
|---|---|---|
| [BE-0009](BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) | Cross-platform abstractions | Implemented |
| [BE-0010](BE-0010-update-scope-statement/BE-0010-update-scope-statement.md) | Update the scope statement | Implemented |
| [BE-0076](BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md) | Selectable browser engines & cross-browser compatibility matrix (web backend) | Implemented |
| [BE-0082](BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md) | Preflight capability check before a run | Implemented |
| [BE-0114](BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) | Driver conformance suite for backend-agnostic behavior | Implemented |
| [BE-0118](BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md) | Unify the wait_for polling contract across drivers | Implemented |
| [BE-0126](BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config.md) | Split Effective into per-platform configs | Implemented |
| [BE-0128](BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md) | Preflight-gate device-control steps by capability | Implemented |
| [BE-0141](BE-0141-backend-lifecycle-protocol/BE-0141-backend-lifecycle-protocol.md) | Bring backend lifecycle into the type system | Implemented |
<!-- /GENERATED:implemented-platform -->

### Backend expansion (iOS actuators)

<!-- GENERATED:implemented-backend -->
| ID | Item | Status |
|---|---|---|
| [BE-0019](BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) | XCUITest backend | Implemented |
| [BE-0020](BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md) | Multi-backend evidence fallback | Implemented |
| [BE-0105](BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md) | Single-snapshot element query for XCUITest | Implemented |
<!-- /GENERATED:implemented-backend -->

### doctor / onboarding

<!-- GENERATED:implemented-doctor -->
| ID | Item | Status |
|---|---|---|
| [BE-0024](BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md) | doctor / onboarding | Implemented |
| [BE-0164](BE-0164-config-aware-environment-installer/BE-0164-config-aware-environment-installer.md) | Config-aware environment installer | Implemented |
<!-- /GENERATED:implemented-doctor -->

### Authoring experience (record / GUI editor)

The AI-driven `record` (Tier 1) is implemented ([recording.md](../docs/recording.md)). These items make the record → edit → re-run cycle easier for humans; the local web UI launcher `bajutsu serve` is the first step.

<!-- GENERATED:implemented-authoring -->
| ID | Item | Status |
|---|---|---|
| [BE-0011](BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) | Local web UI (`bajutsu serve`) | Implemented |
| [BE-0012](BE-0012-action-capture-record/BE-0012-action-capture-record.md) | Action-capture record | Implemented |
| [BE-0013](BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) | Scenario GUI editor | Implemented |
| [BE-0014](BE-0014-record-demarcation/BE-0014-record-demarcation.md) | Demarcation from the existing AI record | Implemented |
| [BE-0044](BE-0044-scenario-provenance/BE-0044-scenario-provenance.md) | Scenario provenance (`from:` — step ↔ natural-language origin) | Implemented |
| [BE-0060](BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) | Download / export a run report as a zip | Implemented |
| [BE-0068](BE-0068-regenerable-reports/BE-0068-regenerable-reports.md) | Regenerable reports (render from stored run data) | Implemented |
| [BE-0072](BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md) | Responsive serve Web UI (small-screen & touch layout) | Implemented |
| [BE-0095](BE-0095-interactive-crawl-graph/BE-0095-interactive-crawl-graph.md) | Interactive crawl graph (draggable nodes + realign) | Implemented |
| [BE-0098](BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface.md) | Unified authoring surface in serve | Implemented |
| [BE-0102](BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md) | Aggregate run-stats dashboard | Implemented |
| [BE-0178](BE-0178-record-multi-action-turn/BE-0178-record-multi-action-turn.md) | Multi-action record turns (batch intra-screen actions) | Implemented |
| [BE-0179](BE-0179-record-human-handoff/BE-0179-record-human-handoff.md) | Human-in-the-loop handoff during record (pause / hand off / resume) | Implemented |
| [BE-0180](BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer.md) | Crawl history viewer in the Web UI | Implemented |
| [BE-0192](BE-0192-record-vision-on-demand/BE-0192-record-vision-on-demand.md) | Vision-on-demand in record (attach a screenshot only when it adds information) | Implemented |
| [BE-0193](BE-0193-record-screenshot-downscale/BE-0193-record-screenshot-downscale.md) | Right-size the record screenshot with a deterministic client-side downscale | Implemented |
| [BE-0194](BE-0194-record-turn-payload-diet/BE-0194-record-turn-payload-diet.md) | Lean the record turn payload (compact the element tree, token-budget controls, per-category usage) | Implemented |
<!-- /GENERATED:implemented-authoring -->

### Self-healing triage (M4)

Lower the maintenance cost of regressions while keeping AI out of the judge role and limited to an investigator.

<!-- GENERATED:implemented-self-healing -->
| ID | Item | Status |
|---|---|---|
| [BE-0021](BE-0021-ai-triage/BE-0021-ai-triage.md) | AI triage (root-cause summary, fix suggestions) | Implemented |
| [BE-0022](BE-0022-update-structured-fixes/BE-0022-update-structured-fixes.md) | `update` (minimal-diff proposals = applying structured fixes) | Implemented |
| [BE-0023](BE-0023-self-healing-guards/BE-0023-self-healing-guards.md) | Guards against "making tests laxer" | Implemented |
<!-- /GENERATED:implemented-self-healing -->

### Candidates from competitive research (MagicPod / Autify)

MagicPod and Autify are built around **AI self-healing + no-code + cloud device farm + visual testing**. Both tools' flagship feature is "AI auto-corrects locators / tap positions during a run", which conflicts directly with Bajutsu's core principle ([DESIGN §2](../DESIGN.md): **keep AI out of the CI gate / determinism first**). Features were evaluated by separating what can be adopted deterministically from what can be adopted only outside the gate.

<!-- GENERATED:implemented-competitive -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0029](BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md) | Visual-regression assertions | Implemented | Both |
| [BE-0030](BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md) | Parameterized shared steps | Implemented | MagicPod |
| [BE-0031](BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md) | Data-driven scenarios | Implemented | MagicPod |
| [BE-0032](BE-0032-secret-variables/BE-0032-secret-variables.md) | Secret variables | Implemented | MagicPod |
| [BE-0033](BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md) | Scenario variables + light control flow | Implemented | MagicPod |
| [BE-0034](BE-0034-tags-selective-runs/BE-0034-tags-selective-runs.md) | Tags / labels + selective runs | Implemented | MagicPod |
| [BE-0035](BE-0035-device-control-primitives/BE-0035-device-control-primitives.md) | Device-control steps (background, status-bar override) | Implemented | MagicPod |
| [BE-0036](BE-0036-utility-steps/BE-0036-utility-steps.md) | HTTP utility step | Implemented | MagicPod |
| [BE-0037](BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md) | WebView / hybrid support | Implemented | MagicPod |
| [BE-0038](BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) | Autonomous crawl exploration (App Explorer style) | Implemented | Autify VAX |
| [BE-0039](BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin.md) | Self-healing limited to "propose + opt-in apply" | Implemented | Both |
| [BE-0046](BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) | OTP & email side-channel steps | Implemented | MagicPod |
| [BE-0052](BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md) | Device-state primitives: timezone, clipboard, shake | Implemented | MagicPod |
| [BE-0165](BE-0165-visual-compare-engines/BE-0165-visual-compare-engines.md) | Selectable perceptual compare engines for visual regression | Implemented |  |
| [BE-0171](BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions.md) | Element-scoped visual assertions and selector-based masking | Implemented |  |
<!-- /GENERATED:implemented-competitive -->

### Candidates from competitive research (Maestro)

Sharpening Bajutsu's determinism-as-contract stance against Maestro's flakiness-tolerance into concrete, machine-checkable features.

<!-- GENERATED:implemented-competitive-maestro -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0047](BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) | AI data sovereignty (provider-agnostic, redacted AI path) | Implemented | Maestro |
| [BE-0048](BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions.md) | Behavioral / protocol assertions | Implemented | Maestro |
| [BE-0049](BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) | Determinism / flakiness audit | Implemented | Maestro |
| [BE-0050](BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) | E2E coverage map | Implemented | Maestro |
| [BE-0097](BE-0097-crawl-ai-data-sovereignty/BE-0097-crawl-ai-data-sovereignty.md) | AI data sovereignty for the crawl guide and serve-spawned AI paths | Implemented |  |
<!-- /GENERATED:implemented-competitive-maestro -->

### Integration & automation (MCP)

<!-- GENERATED:implemented-mcp -->
| ID | Item | Status |
|---|---|---|
| [BE-0017](BE-0017-mcp-server/BE-0017-mcp-server.md) | MCP server | Implemented |
| [BE-0018](BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources.md) | Return evidence as MCP resources | Implemented |
<!-- /GENERATED:implemented-mcp -->

### Development infrastructure (contributor workflow)

Reduce friction for the many parallel sessions working this repo — treat merge conflicts as a design smell and reshape the file flow so independent changes touch disjoint files.

<!-- GENERATED:implemented-dev-infra -->
| ID | Item | Status |
|---|---|---|
| [BE-0043](BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md) | Conflict-resistant file flow (generated indexes, modular files, git hygiene) | Implemented |
| [BE-0061](BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md) | Collision-proof BE-ID allocation (atomic reservation + auto-repair) | Implemented |
| [BE-0065](BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md) | Docstring standard & generated API reference | Implemented |
| [BE-0067](BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md) | Code-quality gate hardening (CI fidelity, security lint, supply-chain) | Implemented |
| [BE-0069](BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md) | Executable contributor guardrails (procedures as commands) | Implemented |
| [BE-0074](BE-0074-be-template-standardization/BE-0074-be-template-standardization.md) | Standardize the BE item template (EN / JA) | Implemented |
| [BE-0078](BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) | Status-driven roadmap folders (proposals / deferred / in-progress / implemented) | Implemented |
| [BE-0089](BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) | Merge-time BE-ID allocation on main | Implemented |
| [BE-0093](BE-0093-public-docs-site/BE-0093-public-docs-site.md) | Public project website & documentation portal (GitHub Pages) | Implemented |
| [BE-0094](BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md) | Generated roadmap status dashboard on GitHub Pages | Implemented |
| [BE-0096](BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md) | Keep docs links to roadmap items from rotting on promotion | Implemented |
| [BE-0100](BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md) | Progress tracking and cross-item relations in the BE template | Implemented |
| [BE-0103](BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md) | Right-size the model and reasoning effort per development task | Implemented |
| [BE-0109](BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) | GitHub Issues as the ownership tracker for open roadmap items | Implemented |
| [BE-0112](BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement.md) | Enforce core / contract / periphery layer boundaries in the gate | Implemented |
| [BE-0113](BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) | Realign DESIGN.md with the current implementation | Implemented |
| [BE-0117](BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md) | Cover the rest of the CLI command layer, then ratchet the coverage floor | Implemented |
| [BE-0122](BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility.md) | Legible GitHub Actions workflow and job names | Implemented |
| [BE-0139](BE-0139-roadmap-dashboard-issue-links/BE-0139-roadmap-dashboard-issue-links.md) | Link the roadmap dashboard and item files to their tracking issue | Implemented |
| [BE-0149](BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail.md) | Close the roadmap-placeholder format-guardrail gap | Implemented |
| [BE-0156](BE-0156-roadmap-topic-label-sync/BE-0156-roadmap-topic-label-sync.md) | Keep roadmap-item PR labels in sync with Topic | Implemented |
| [BE-0159](BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md) | Flatten roadmap items into one directory (retire status-driven folders) | Implemented |
| [BE-0162](BE-0162-roadmap-status-filter-skill/BE-0162-roadmap-status-filter-skill.md) | Roadmap status-filter skill for AI sessions | Implemented |
| [BE-0203](BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md) | Claude Code as the automated PR code reviewer | Implemented |
| [BE-0213](BE-0213-glossary-and-docs-structure/BE-0213-glossary-and-docs-structure.md) | Terminology glossary and documentation structure review | Implemented |
| [BE-0216](BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill.md) | propose-and-build: author a BE proposal and its implementation in parallel, stacked | Implemented |
<!-- /GENERATED:implemented-dev-infra -->

### Codebase quality & technical debt

Behavior-preserving cleanup inside `bajutsu/` itself — deduplication, decomposition of oversized functions/modules, and naming clarity — as distinct from *Development infrastructure (contributor workflow)* above, which covers the tooling contributors use to work on this repo (CI, hooks, roadmap automation).

<!-- GENERATED:implemented-quality-debt -->
| ID | Item | Status |
|---|---|---|
| [BE-0092](BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md) | Extract the crawl coordinator into a class | Implemented |
| [BE-0132](BE-0132-dedupe-crawl-screenshot-helpers/BE-0132-dedupe-crawl-screenshot-helpers.md) | Deduplicate crawl screenshot helpers | Implemented |
| [BE-0134](BE-0134-serve-cli-flag-mirror-drift/BE-0134-serve-cli-flag-mirror-drift.md) | Eliminate serve-to-CLI flag-mirror drift | Implemented |
| [BE-0135](BE-0135-module-naming-debt/BE-0135-module-naming-debt.md) | Resolve top-level module naming debt | Implemented |
| [BE-0140](BE-0140-dedupe-claude-client-init/BE-0140-dedupe-claude-client-init.md) | Deduplicate Claude client initialization | Implemented |
| [BE-0142](BE-0142-cli-command-coverage/BE-0142-cli-command-coverage.md) | Cover the CLI command layer | Implemented |
| [BE-0143](BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md) | Decompose the run command god-function | Implemented |
| [BE-0150](BE-0150-scenario-load-yaml-error-handling/BE-0150-scenario-load-yaml-error-handling.md) | Fail cleanly on a malformed scenario in `trace --explain` and `audit` | Implemented |
| [BE-0172](BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition.md) | Decompose the run-path step loop and per-scenario runner | Implemented |
| [BE-0197](BE-0197-environment-protocol-shape/BE-0197-environment-protocol-shape.md) | Even out the Environment protocol shape for a third platform | Implemented |
| [BE-0198](BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split.md) | Split the JobRegistry out of ServeState | Implemented |
| [BE-0199](BE-0199-doctor-screen-probe-dedupe/BE-0199-doctor-screen-probe-dedupe.md) | Share the doctor screen probe between CLI and serve | Implemented |
| [BE-0200](BE-0200-run-id-contract/BE-0200-run-id-contract.md) | Make the run-id format a single named contract | Implemented |
| [BE-0201](BE-0201-record-enrich-shared-replay/BE-0201-record-enrich-shared-replay.md) | Consolidate the duplicated replay helpers of record and enrich | Implemented |
| [BE-0202](BE-0202-serve-js-modularization/BE-0202-serve-js-modularization.md) | Split serve.js into section files without a build step | Implemented |
| [BE-0206](BE-0206-serve-state-module-split/BE-0206-serve-state-module-split.md) | Split serve job state from job execution | Implemented |
<!-- /GENERATED:implemented-quality-debt -->

### Dogfood fixtures (demo apps)

Purpose-built test subjects that exercise the commands end-to-end. The showcase suite is the next-generation dogfood target — the same app written in UIKit and SwiftUI, each in an accessibility-on / accessibility-off variant (four products, two codebases), so `run` (id-based), `record` (no-id fallback), `doctor` (Ready vs Blocked), and `crawl` all have one rich, representative subject. The screen-by-screen contract lives in [`demos/showcase/SPEC.md`](../demos/showcase/SPEC.md).

<!-- GENERATED:implemented-dogfood -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0045](BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md) | Dogfood showcase apps (UIKit × SwiftUI, accessibility-paired) | Implemented | Dogfooding |
| [BE-0079](BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase.md) | Consolidate the demo & dogfood apps onto the showcase suite | Implemented | Dogfooding |
<!-- /GENERATED:implemented-dogfood -->

### Dogfood fixtures (web UI)

Bajutsu's own `serve` Web UI is a web app, so the Web (Playwright) backend drives it — a deterministic, Tier-2 regression net for the UI, the web-side counterpart to the iOS [BE-0045](implemented/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md) showcase fixtures.

<!-- GENERATED:implemented-dogfood-web-ui -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0058](BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) | Dogfood the serve Web UI (web-backend regression net) | Implemented | Dogfooding |
| [BE-0059](BE-0059-launch-target-server/BE-0059-launch-target-server.md) | Bring up the target server for a run (`launchServer`) | Implemented | Dogfooding |
| [BE-0189](BE-0189-serve-ui-dogfood-ci-gate/BE-0189-serve-ui-dogfood-ci-gate.md) | Gate the serve Web UI dogfood in CI | Implemented | Dogfooding |
<!-- /GENERATED:implemented-dogfood-web-ui -->

### AI provider configuration

The Tier-1 AI paths (`record` / `triage` / `--dismiss-alerts` / `crawl`) call Claude through a pluggable provider. This topic covers selecting and configuring that provider — e.g. Amazon Bedrock (authenticated with AWS credentials) as an alternative to the direct Anthropic API. The deterministic `run` / CI gate calls no model and is unaffected.

<!-- GENERATED:implemented-ai-provider -->
| ID | Item | Status |
|---|---|---|
| [BE-0053](BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md) | Amazon Bedrock as a pluggable AI provider | Implemented |
| [BE-0101](BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md) | Legible Claude-using / Claude-free split with a zero-config non-AI path | Implemented |
| [BE-0104](BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) | Vendor-neutral AI backend interface | Implemented |
| [BE-0111](BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency.md) | Make the AI SDK an optional extra so the deterministic gate installs AI-free | Implemented |
| [BE-0163](BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) | Replace the Claude Code CLI authoring backend with an `ant`-CLI OAuth AI provider | Implemented |
| [BE-0175](BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login.md) | Sign in to the `ant` provider from the serve Web UI | Implemented |
| [BE-0176](BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md) | Revive Claude Code as an AiBackend adapter with file-based vision | Implemented |
| [BE-0183](BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings.md) | Per-provider AI settings in the serve Web UI | Implemented |
| [BE-0188](BE-0188-configurable-ai-output-language/BE-0188-configurable-ai-output-language.md) | Configurable AI output language for record and crawl | Implemented |
<!-- /GENERATED:implemented-ai-provider -->

### Hosting the web UI (cloud / self-hosted)

Standing up `bajutsu serve` beyond loopback. The hardening that makes the existing stdlib server safe to expose (auth, input validation) has shipped; the full hosted topologies remain proposals below.

<!-- GENERATED:implemented-hosting -->
| ID | Item | Status |
|---|---|---|
| [BE-0016](BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) | Self-hosting of the web UI | Implemented |
| [BE-0051](BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) | Serve hardening for hosting (auth, input validation) | Implemented |
| [BE-0055](BE-0055-operational-logging/BE-0055-operational-logging.md) | Operational logging for the hosted serve | Implemented |
| [BE-0090](BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md) | Govern and sandbox command execution from uploaded bundle configs | Implemented |
| [BE-0106](BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) | Post-completion worker model (eliminate Redis dependency) | Implemented |
| [BE-0108](BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md) | Restrict config sources to upload and Git when hosted | Implemented |
| [BE-0110](BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md) | Evidence upload to object storage via URI | Implemented |
| [BE-0127](BE-0127-split-serve-operations-module/BE-0127-split-serve-operations-module.md) | Split the serve operations god-module | Implemented |
| [BE-0129](BE-0129-serve-scope-boundary/BE-0129-serve-scope-boundary.md) | Bound serve scope and keep host concerns out of shared config | Implemented |
| [BE-0160](BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md) | Credential-free worker uploads via presigned URLs | Implemented |
| [BE-0169](BE-0169-serve-metrics-observability/BE-0169-serve-metrics-observability.md) | Serve metrics and observability endpoint | Implemented |
| [BE-0173](BE-0173-slim-web-worker-image/BE-0173-slim-web-worker-image.md) | Slim Linux web-worker container image | Implemented |
| [BE-0190](BE-0190-org-scoped-crawl-history/BE-0190-org-scoped-crawl-history.md) | Org-scoped crawl history on the server backend | Implemented |
| [BE-0204](BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support.md) | GCS support for server-side object storage | Implemented |
<!-- /GENERATED:implemented-hosting -->

### Security hardening

Closing the edges the deterministic core does not touch — `serve`'s HTTP surface, how secrets flow through capture / record / artifacts, driver argument hygiene, and the CI supply chain. These items keep the tool safe to run on a shared machine and safe to hand a scenario from an untrusted source, without weakening the prime directives.

<!-- GENERATED:implemented-security -->
| ID | Item | Status |
|---|---|---|
| [BE-0115](BE-0115-inprocess-collector-auth/BE-0115-inprocess-collector-auth.md) | Authenticate the in-process iOS network collector | Implemented |
| [BE-0116](BE-0116-udid-argument-validation/BE-0116-udid-argument-validation.md) | Tighten UDID validation against argument injection | Implemented |
| [BE-0120](BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization.md) | Tokenize secrets in recorded scenario YAML | Implemented |
| [BE-0121](BE-0121-serve-csrf-host-allowlist/BE-0121-serve-csrf-host-allowlist.md) | Unconditional CSRF and Host-allowlist defenses for serve | Implemented |
| [BE-0123](BE-0123-composite-action-input-indirection/BE-0123-composite-action-input-indirection.md) | Route composite-action inputs through env indirection | Implemented |
| [BE-0124](BE-0124-config-source-owner-repo-validation/BE-0124-config-source-owner-repo-validation.md) | Tighten config-source owner and repo validation | Implemented |
| [BE-0125](BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction.md) | Restrict the claude-code authoring agent tools | Implemented |
| [BE-0130](BE-0130-default-network-secret-redaction/BE-0130-default-network-secret-redaction.md) | Redact sensitive network headers and cookies by default | Implemented |
| [BE-0131](BE-0131-run-artifact-permissions/BE-0131-run-artifact-permissions.md) | Restrict run-artifact file permissions | Implemented |
| [BE-0133](BE-0133-pin-actionlint-installer/BE-0133-pin-actionlint-installer.md) | Pin the actionlint installer by SHA | Implemented |
| [BE-0136](BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md) | Write-once secrets store for serve | Implemented |
| [BE-0144](BE-0144-automerge-stale-approval-race/BE-0144-automerge-stale-approval-race.md) | Close the auto-merge stale-approval race | Implemented |
| [BE-0151](BE-0151-screenshot-secret-capture-warning/BE-0151-screenshot-secret-capture-warning.md) | Warn when screenshots and video may capture on-screen secrets | Implemented |
| [BE-0152](BE-0152-totp-seed-artifact-leak/BE-0152-totp-seed-artifact-leak.md) | Keep literal TOTP seeds out of run artifacts | Implemented |
| [BE-0153](BE-0153-encode-aware-secret-redaction/BE-0153-encode-aware-secret-redaction.md) | Encode-aware secret redaction | Implemented |
| [BE-0155](BE-0155-idb-input-text-via-stdin/BE-0155-idb-input-text-via-stdin.md) | Pass idb input text via stdin to keep secrets out of argv | Implemented |
| [BE-0174](BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md) | Contain scenario component and data refs within the suite root | Implemented |
<!-- /GENERATED:implemented-security -->

### Configuration sourcing

Where a project's config and scenarios come from. A Git repository + ref is a today-runnable source for CI and a self-hosted `serve`, materialized at an immutable commit.

<!-- GENERATED:implemented-config-sourcing -->
| ID | Item | Status |
|---|---|---|
| [BE-0063](BE-0063-git-config-source/BE-0063-git-config-source.md) | Load config (and its scenario tree) from a Git repository + ref | Implemented |
| [BE-0073](BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) | Upload a config + scenarios + app-binary bundle as a zip and run it from the web UI | Implemented |
| [BE-0119](BE-0119-scenario-schema-versioning/BE-0119-scenario-schema-versioning.md) | Version the scenario schema for cross-version reads | Implemented |
| [BE-0177](BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md) | Per-target config defaults for run-behavior settings | Implemented |
| [BE-0187](BE-0187-serve-config-view/BE-0187-serve-config-view.md) | View the loaded config in the serve Web UI (raw YAML, structured tree, Git provenance) | Implemented |
<!-- /GENERATED:implemented-config-sourcing -->

### Surfacing CLI features in the serve Web UI

Bringing the CLI's own tools into the serve Web UI, where authoring happens. The scenario editor's inline lint / schema validation landed first.

<!-- GENERATED:implemented-serve-cli-features -->
| ID | Item | Status |
|---|---|---|
| [BE-0137](BE-0137-serve-codegen/BE-0137-serve-codegen.md) | Generate native test code from the serve Web UI | Implemented |
| [BE-0138](BE-0138-serve-lint/BE-0138-serve-lint.md) | Inline scenario validation in the serve editor | Implemented |
| [BE-0145](BE-0145-serve-audit/BE-0145-serve-audit.md) | Determinism audit in the serve Web UI | Implemented |
| [BE-0146](BE-0146-serve-coverage/BE-0146-serve-coverage.md) | E2E coverage map in the serve Web UI | Implemented |
| [BE-0147](BE-0147-serve-triage/BE-0147-serve-triage.md) | Triage failed runs in the serve Web UI | Implemented |
| [BE-0148](BE-0148-serve-doctor/BE-0148-serve-doctor.md) | Doctor readiness panel in the serve Web UI | Implemented |
<!-- /GENERATED:implemented-serve-cli-features -->

### AI usage and cost observability

Making Bajutsu's AI token and dollar spend visible: an attributed, persistent ledger of every AI call, and (separately) the serve Web UI dashboard that reads it.

<!-- GENERATED:implemented-ai-usage -->
| ID | Item | Status |
|---|---|---|
| [BE-0195](BE-0195-ai-usage-cost-dashboard/BE-0195-ai-usage-cost-dashboard.md) | Visualize AI token usage and cost in the serve Web UI | Implemented |
| [BE-0196](BE-0196-ai-usage-cost-ledger/BE-0196-ai-usage-cost-ledger.md) | Record AI token usage and cost as an attributed, persistent ledger | Implemented |
<!-- /GENERATED:implemented-ai-usage -->

### codegen coverage

Turning a passing scenario into a native test in a destination framework's idiom. The web (Playwright) target has landed alongside the original XCUITest one.

<!-- GENERATED:implemented-codegen -->
| ID | Item | Status |
|---|---|---|
| [BE-0025](BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation.md) | Coordinate swipe generation | Implemented |
| [BE-0026](BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) | Shrink unsupported syntax | Implemented |
| [BE-0062](BE-0062-playwright-codegen/BE-0062-playwright-codegen.md) | Playwright codegen target | Implemented |
| [BE-0083](BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md) | Unify the codegen emitters behind a shared scenario walk | Implemented |
| [BE-0085](BE-0085-shrink-web-codegen-syntax/BE-0085-shrink-web-codegen-syntax.md) | Shrink unsupported web (Playwright) codegen syntax | Implemented |
<!-- /GENERATED:implemented-codegen -->

### Crawl performance / scale-out

Running the autonomous crawl across more than one device so a full screen map is built in a fraction of the wall-clock time.

<!-- GENERATED:implemented-crawl -->
| ID | Item | Status |
|---|---|---|
| [BE-0064](BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) | Parallel crawl across multiple simulators | Implemented |
| [BE-0077](BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md) | Parallel web crawl across multiple browsers | Implemented |
| [BE-0181](BE-0181-crawl-continuation/BE-0181-crawl-continuation.md) | Resumable crawl continuation (Web UI + full-frontier resume) | Implemented |
<!-- /GENERATED:implemented-crawl -->

### On-device validation (M1 close-out)

<!-- GENERATED:implemented-on-device -->
| ID | Item | Status |
|---|---|---|
| [BE-0005](BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md) | idb_companion version monitoring | Implemented |
| [BE-0006](BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) | idb element-tree normalization accuracy | Implemented |
| [BE-0087](BE-0087-idb-action-settle/BE-0087-idb-action-settle.md) | idb action timing robustness (settle before actuation) | Implemented |
| [BE-0088](BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md) | Overlap the Simulator boot with the build | Implemented |
| [BE-0207](BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md) | Make the XCUITest runner channel robust to transient timeouts | Implemented |
| [BE-0218](BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md) | Stabilize the E2E Simulator gate: namespace-aware readiness and a bounded actuation timeout | Implemented |
<!-- /GENERATED:implemented-on-device -->

### Integration with external services

<!-- GENERATED:implemented-external-integration -->
| ID | Item | Status |
|---|---|---|
| [BE-0099](BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md) | Webhook notifications for run results | Implemented |
| [BE-0161](BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md) | Export run results in Common Test Report Format (CTRF) | Implemented |
<!-- /GENERATED:implemented-external-integration -->

### Miscellaneous / on hold

<!-- GENERATED:implemented-misc -->
| ID | Item | Status |
|---|---|---|
| [BE-0028](BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard.md) | Guard against over-matching evidence rules | Implemented |
<!-- /GENERATED:implemented-misc -->

## In progress

Accepted and actively being built — a PR is in flight or imminent.

### Platform expansion (landed slices)

The Web (Playwright) backend and its completion (rich capabilities, parallel runs) — the rich end of the capability model, on the existing Linux gate.

<!-- GENERATED:in-progress-platform-landed -->

<!-- /GENERATED:in-progress-platform-landed -->

### Platform expansion (Android / Web / Flutter)

<!-- GENERATED:in-progress-platform -->
| ID | Item | Status |
|---|---|---|
| [BE-0007](BE-0007-android-backend/BE-0007-android-backend.md) | Android backend | In progress |
| [BE-0208](BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) | Android on-device e2e in CI (emulator via KVM) | In progress |
<!-- /GENERATED:in-progress-platform -->

### Candidates from competitive research (MagicPod / Autify)

<!-- GENERATED:in-progress-competitive -->

<!-- /GENERATED:in-progress-competitive -->

### Candidates from competitive research (Maestro)

<!-- GENERATED:in-progress-competitive-maestro -->

<!-- /GENERATED:in-progress-competitive-maestro -->

### Backend expansion (iOS actuators)

<!-- GENERATED:in-progress-backend -->

<!-- /GENERATED:in-progress-backend -->

### Development infrastructure (contributor workflow)

Reduce friction for the many parallel sessions working this repo — treat merge conflicts as a design smell and reshape the file flow so independent changes touch disjoint files.

<!-- GENERATED:in-progress-dev-infra -->

<!-- /GENERATED:in-progress-dev-infra -->

### Dogfood fixtures (demo apps)

Consolidating the demo and dogfood apps onto the showcase suite: bringing it to parity with the legacy `sample` / `demo` / `sample2` fixtures (codegen → XCUITest, visual regression, gesture targets, the evidence tour), re-pointing the demos and on-device CI at it, and retiring the three legacy apps — so the showcase becomes the single iOS fixture.

<!-- GENERATED:in-progress-dogfood -->

<!-- /GENERATED:in-progress-dogfood -->

### Hosting the web UI (cloud / self-hosted)

<!-- GENERATED:in-progress-hosting -->
| ID | Item | Status |
|---|---|---|
| [BE-0015](BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) | Public hosting of the web UI | In progress |
<!-- /GENERATED:in-progress-hosting -->

### Surfacing CLI features in the serve Web UI

Bringing the CLI's own tools into the serve Web UI, where authoring happens. The scenario editor's inline lint / schema validation landed first.

<!-- GENERATED:in-progress-serve-cli-features -->

<!-- /GENERATED:in-progress-serve-cli-features -->

### codegen coverage

<!-- GENERATED:in-progress-codegen -->

<!-- /GENERATED:in-progress-codegen -->

### On-device validation (M1 close-out)

<!-- GENERATED:in-progress-on-device -->

<!-- /GENERATED:in-progress-on-device -->

### Authoring experience (record / GUI editor)

<!-- GENERATED:in-progress-authoring -->
| ID | Item | Status |
|---|---|---|
| [BE-0191](BE-0191-pluggable-theme-system-serve-ui/BE-0191-pluggable-theme-system-serve-ui.md) | Pluggable theme system for the serve Web UI (visual tokens and swappable transitions) | In progress |
<!-- /GENERATED:in-progress-authoring -->

### Codebase quality & technical debt

<!-- GENERATED:in-progress-quality-debt -->

<!-- /GENERATED:in-progress-quality-debt -->

## Proposals

Under consideration — not yet decided. Promote an item to *In progress* once work starts, or to *Implemented* when it ships.

### On-device validation (M1 close-out)

The deterministic core runs end-to-end on the FakeDriver, and the idb backend's subprocess execution (`describe-all` parsing, frame-center tap/text/swipe) and the simctl launch sequence are validated on a real device. What remains is only ongoing maintenance monitoring.

<!-- GENERATED:proposals-on-device -->

<!-- /GENERATED:proposals-on-device -->

### Platform expansion (Android / Web / Flutter)

The scope is currently **limited to the iOS Simulator** ([DESIGN §1](../DESIGN.md)). This section covers going multi-platform by leveraging the driver / backend abstractions. The big-picture overview is in [multi-platform.md](../docs/multi-platform.md); the concrete per-platform design lives in the items below: [BE-0009](proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) holds the shared abstractions, then Web (recommended first), Android, and Flutter.

<!-- GENERATED:proposals-platform -->
| ID | Item | Status |
|---|---|---|
| [BE-0008](BE-0008-flutter-support/BE-0008-flutter-support.md) | Flutter support | Proposal |
| [BE-0210](BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) | Android on-device actuation fidelity | Proposal |
| [BE-0211](BE-0211-android-device-control/BE-0211-android-device-control.md) | Android device control (setLocation, clipboard) | Proposal |
| [BE-0212](BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md) | Split the coarse deviceControl capability into per-operation tokens | Proposal |
<!-- /GENERATED:proposals-platform -->

### Authoring experience (record / GUI editor)

<!-- GENERATED:proposals-authoring -->
| ID | Item | Status |
|---|---|---|
| [BE-0182](BE-0182-record-human-value-prompt/BE-0182-record-human-value-prompt.md) | Human value entry during record (OTP / random / one-off values) | Proposal |
| [BE-0185](BE-0185-record-human-takeover-step/BE-0185-record-human-takeover-step.md) | Human takeover step during record (CAPTCHA / biometrics / unresolvable gestures) | Proposal |
<!-- /GENERATED:proposals-authoring -->

### Surfacing CLI features in the serve Web UI

<!-- GENERATED:proposals-serve-cli-features -->

<!-- /GENERATED:proposals-serve-cli-features -->

### Dogfood fixtures (demo apps)

<!-- GENERATED:proposals-dogfood -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0107](BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut.md) | Reach every showcase tab by navigation, not a launch-env shortcut | Proposal | Dogfooding |
<!-- /GENERATED:proposals-dogfood -->

### Dogfood fixtures (web UI)

<!-- GENERATED:proposals-dogfood-web-ui -->

<!-- /GENERATED:proposals-dogfood-web-ui -->

### AI provider configuration

<!-- GENERATED:proposals-ai-provider -->
| ID | Item | Status |
|---|---|---|
| [BE-0184](BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md) | Persist serve AI provider settings across restarts | Proposal |
| [BE-0215](BE-0215-claude-code-oauth-token-credential/BE-0215-claude-code-oauth-token-credential.md) | Explicit CLAUDE_CODE_OAUTH_TOKEN credential for the claude-code provider | Proposal |
<!-- /GENERATED:proposals-ai-provider -->

### AI usage and cost observability

Measuring what the AI paths (`record` / `crawl` / `triage --ai` / `run --apply`) actually cost — tokens and money — broken down by provider, model, command, and scenario. Bajutsu already has `bajutsu/usage.py`, but it tracks a single in-memory token total that is lost on process exit; these items turn that into an attributed, persistent record and surface it in the serve Web UI, so a team can see where its AI spend goes and make deterministic provider/model choices from real data. Observability only — nothing here puts an LLM on the `run` / CI verdict path.

<!-- GENERATED:proposals-ai-usage -->

<!-- /GENERATED:proposals-ai-usage -->

### Hosting the web UI (cloud / self-hosted)

Turn the local `bajutsu serve` launcher into a shared service. The runner drives an iOS Simulator and so needs a Mac, which forces a control-plane (Linux) ⇄ macOS-worker split. [BE-0015](in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) selects a managed, multi-tenant public stack; [BE-0016](in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) covers running it on your own Mac(s).

<!-- GENERATED:proposals-hosting -->
| ID | Item | Status |
|---|---|---|
| [BE-0166](BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md) | Capability-routed job queues | Proposal |
| [BE-0167](BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md) | Control-plane scale-out behind a load balancer | Proposal |
| [BE-0168](BE-0168-self-host-high-availability/BE-0168-self-host-high-availability.md) | Self-hosted high availability and single-point-of-failure hardening | Proposal |
| [BE-0170](BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch.md) | Weighted-fair cross-org job dispatch | Proposal |
<!-- /GENERATED:proposals-hosting -->

### Security hardening

Closing the edges the deterministic core does not touch — `serve`'s HTTP surface, how secrets flow through capture / record / artifacts, driver argument hygiene, and the CI supply chain. These items keep the tool safe to run on a shared machine and safe to hand a scenario from an untrusted source, without weakening the prime directives.

<!-- GENERATED:proposals-security -->

<!-- /GENERATED:proposals-security -->

### Configuration sourcing

Where `bajutsu` reads its config and scenario tree from. Today that is a local path; the items here propose naming a **Git repository at a ref** (`github:owner/repo@ref:path`) or uploading a bundle, so a hosted or self-hosted `serve`, or a CI runner, can pull a team's test repo directly.

<!-- GENERATED:proposals-config-sourcing -->

<!-- /GENERATED:proposals-config-sourcing -->

### codegen coverage

Turning a passing scenario into a native test in a destination framework's idiom. These items shrink the range of constructs an emitter drops to a `// TODO`.

<!-- GENERATED:proposals-codegen -->
| ID | Item | Status |
|---|---|---|
| [BE-0209](BE-0209-android-codegen-emitter/BE-0209-android-codegen-emitter.md) | Android codegen emitter (Espresso / UI Automator) | Proposal |
<!-- /GENERATED:proposals-codegen -->

### Crawl performance / scale-out

Keeping the autonomous crawl fast and its code lean as it grows.

<!-- GENERATED:proposals-crawl -->

<!-- /GENERATED:proposals-crawl -->

### Backend expansion (iOS actuators)

<!-- GENERATED:proposals-backend -->

<!-- /GENERATED:proposals-backend -->

### doctor / onboarding

<!-- GENERATED:proposals-doctor -->

<!-- /GENERATED:proposals-doctor -->

### Development infrastructure (contributor workflow)

<!-- GENERATED:proposals-dev-infra -->
| ID | Item | Status |
|---|---|---|
| [BE-0214](BE-0214-web-only-beginner-tutorial/BE-0214-web-only-beginner-tutorial.md) | Web-only beginner tutorial (no Xcode/Simulator required) | Proposal |
| [BE-0217](BE-0217-harden-review-prompt/BE-0217-harden-review-prompt.md) | Harden the automated PR review prompt with research-backed policy | Proposal |
<!-- /GENERATED:proposals-dev-infra -->

### Codebase quality & technical debt

Behavior-preserving cleanup inside `bajutsu/` itself — deduplication, decomposition of oversized functions/modules, and naming clarity — as distinct from *Development infrastructure (contributor workflow)* above, which covers the tooling contributors use to work on this repo (CI, hooks, roadmap automation).

<!-- GENERATED:proposals-quality-debt -->
| ID | Item | Status |
|---|---|---|
| [BE-0205](BE-0205-crawl-command-decomposition/BE-0205-crawl-command-decomposition.md) | Decompose the crawl CLI command like run | Proposal |
<!-- /GENERATED:proposals-quality-debt -->

### Integration with external services

Sending a run's result out to a service the team already lives in. These are post-verdict, deterministic transports — they carry the verdict the runner already computed, never an LLM's, and a delivery failure never moves the run's result.

<!-- GENERATED:proposals-external-integration -->

<!-- /GENERATED:proposals-external-integration -->

### Candidates from competitive research (MagicPod / Autify)

<!-- GENERATED:proposals-competitive -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0186](BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md) | Mailbox provider registry for the email step | Proposal |  |
<!-- /GENERATED:proposals-competitive -->

### Candidates from competitive research (Maestro)

Maestro (mobile.dev) is an open-source, cross-platform UI E2E tool whose direction leans into breadth, a hosted device cloud, and AI features that are *optional / advisory by default*. These items sharpen Bajutsu's opposite stance — determinism as a contract, verification below the UI, and AI strictly under the user's control.

<!-- GENERATED:proposals-competitive-maestro -->

<!-- /GENERATED:proposals-competitive-maestro -->

## Deferred

Parked proposals — considered, then shelved for now. Kept here (not deleted) so the decision and its rationale stay on record; un-defer by changing `Status` back to `Proposal`.

### Candidates from competitive research (MagicPod / Autify)

<!-- GENERATED:deferred-competitive -->
| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0040](BE-0040-ai-assertions/BE-0040-ai-assertions.md) | AI assertions | Deferred | MagicPod |
| [BE-0157](BE-0157-shake-device-primitive/BE-0157-shake-device-primitive.md) | Shake device primitive | Deferred | MagicPod |
| [BE-0158](BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive.md) | Timezone device primitive | Deferred | MagicPod |
<!-- /GENERATED:deferred-competitive -->

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
- **Cloud device farm / real-device / cloud execution** — out of the current iOS-Simulator-only scope ([DESIGN §1](../DESIGN.md)). Multi-platform is tracked as proposals (the *Platform expansion* items).
- **Per-step screenshots / UI tree on error / device logs** — already covered by the evidence subsystem (capturePolicy + the `result:error` safety net).
- **NL→test generation (Autopilot equivalent)** — overlaps with the existing `record` + the *Authoring experience* items.
- **Scheduling / Slack / TestRail integration** — the domain of the CI / notification layer. Low priority (separately, if needed).
- **Automatic retry of failed tests** — in tension with determinism-first (no fixed sleeps, condition waits). It can hide flakiness, so if adopted at all it should be limited to quarantine use and needs careful consideration.

---

## Unsorted ideas

> Add unformed thoughts here. Promote them to a numbered BE item later.

-
