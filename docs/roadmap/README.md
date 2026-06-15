**English** · [日本語](README-ja.md)

# Bajutsu roadmap / backlog

> A **living document** that gathers the features we want to build next. Each item is its own
> file (one BE ID per item); drop half-formed thoughts into [Unsorted ideas](#unsorted-ideas)
> first, then promote them into a numbered item once they firm up.
>
> - **The accurate list of what exists today (implemented / unwired)** lives in
>   [architecture.md#implementation-status](../architecture.md#implementation-status) — the source
>   of truth. This page is about what's *next*.
> - The design background (the *why*) is in [`DESIGN.md`](../../DESIGN.md).
> - The **strategic shape of the whole (the north star)** is in [vision.md](../vision.md).

## Legend

**Priority** — `P0` (do next) / `P1` (will do) / `P2` (nice to have) / `P3` (idea stage)
**Status** — 💡 idea / 📋 planned / 🚧 in progress / ❄️ on hold / ✅ done

## Adding a roadmap item — BE IDs (agents MUST follow)

Every roadmap item is a directory `BE-NNNN-<slug>/` holding the English file `BE-NNNN-<slug>.md`
and its Japanese version `BE-NNNN-<slug>-ja.md` (same ID and slug). **BE** stands for *Bajutsu
Evolution* and `NNNN` is a **zero-padded, 4-digit, monotonically increasing** ID.

When you add a roadmap item:

1. **Allocate the next ID** = the highest existing `BE-NNNN` + 1. Find it with:
   ```bash
   ls -d docs/roadmap/BE-*/ | sort | tail -1
   ```
   Never reuse, skip, or guess a number.
2. **Create the item directory and both language files** —
   `docs/roadmap/BE-NNNN-<slug>/BE-NNNN-<slug>.md` (English) and
   `docs/roadmap/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md` (Japanese, same ID & slug) — and add a row to
   the matching topic table in **both** index pages (`README.md` and `README-ja.md`).
3. **IDs are permanent.** Never renumber an existing item — not when its status changes, not when
   it is completed, not when it is removed from a table. A BE ID, once assigned, refers to that
   item forever.

Each file follows the **Swift-Evolution proposal format**: a metadata block (`* Proposal`,
`* Status`, `* Track`, `* Topic`, …) then `## Introduction` / `## Motivation` /
`## Detailed design` / `## Alternatives considered` / `## References` (fill what you can; mark
unknowns `TBD`). The **Status** decides the track: `Implemented` or `Accepted, in progress` are
listed under **Accepted** (a decision/implementation record); `Proposal` or `Proposal (deferred)`
under **Proposals** (under consideration). Update an item's Status as it advances rather than
moving the file.

---

## Accepted

Decisions that have been made — already implemented, or accepted and to be implemented. This is the project's **decision & implementation record**.

### Milestones (M1–M4)

Coarse delivery milestones (M1–M4) — the project's **decision & implementation record**. All four are accepted and shipped; the finer-grained features they decomposed into live in the topic sections below.

| ID | Item | Status |
|---|---|---|
| [BE-0001](BE-0001-m1-deterministic-runner/BE-0001-m1-deterministic-runner.md) | Deterministic runner (M1) | Implemented |
| [BE-0002](BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence.md) | AI authoring loop & evidence (M2) | Implemented |
| [BE-0003](BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md) | codegen, traces, network & CI (M3) | Implemented |
| [BE-0004](BE-0004-m4-self-healing-triage/BE-0004-m4-self-healing-triage.md) | Self-healing triage (M4) | Implemented |

### Authoring experience (record / GUI editor)

The AI-driven `record` (Tier 1) is implemented ([recording.md](../recording.md)). The aim here is **non-AI action capture** and **visual editing of scenarios**, to make the record → edit → re-run round trip easy for humans. The local web UI launcher `bajutsu serve` is the first step toward this.

| ID | Item | Status |
|---|---|---|
| [BE-0011](BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) | Local web UI (`bajutsu serve`) | Implemented |

### Self-healing triage (M4)

Lower the maintenance cost of regressions while keeping AI out of the judge role and limited to an investigator.

| ID | Item | Status |
|---|---|---|
| [BE-0021](BE-0021-ai-triage/BE-0021-ai-triage.md) | AI triage (root-cause summary, fix suggestions) | Implemented |
| [BE-0022](BE-0022-update-structured-fixes/BE-0022-update-structured-fixes.md) | `update` (minimal-diff proposals = applying structured fixes) | Implemented |
| [BE-0023](BE-0023-self-healing-guards/BE-0023-self-healing-guards.md) | Guards against "making tests laxer" | Implemented |

### Candidates from competitive research (MagicPod / Autify)

MagicPod and Autify have **AI self-healing + no-code + cloud device farm + visual testing** in their DNA. Both companies' flagship feature is "AI auto-corrects locators / tap positions during a run", which collides head-on with Bajutsu's core ([DESIGN §2](../../DESIGN.md): **keep AI out of the CI gate / determinism first**). So we evaluated them split into what we can adopt deterministically and what we can adopt only outside the gate.

| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0030](BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md) | Parameterized shared steps | Implemented | MagicPod |
| [BE-0031](BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md) | Data-driven scenarios | Implemented | MagicPod |
| [BE-0032](BE-0032-secret-variables/BE-0032-secret-variables.md) | Secret variables | Implemented | MagicPod |
| [BE-0033](BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md) | Scenario variables + light control flow | In progress | MagicPod |
| [BE-0034](BE-0034-tags-selective-runs/BE-0034-tags-selective-runs.md) | Tags / labels + selective runs | Implemented | MagicPod |
| [BE-0039](BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin.md) | Self-healing limited to "propose + opt-in apply" | Implemented | Both |

## Proposals

Under consideration — not yet decided. Promote an item to *Accepted* once a decision is made.

### On-device validation (M1 close-out)

The deterministic core runs end-to-end on the FakeDriver, and the idb backend's subprocess execution (`describe-all` parsing, frame-center tap/text/swipe) and the simctl launch sequence are validated on a real device (iPhone 17 Pro / latest iOS). What remains is only ongoing maintenance monitoring.

| ID | Item | Status |
|---|---|---|
| [BE-0005](BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring.md) | idb_companion version monitoring | Proposal |
| [BE-0006](BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) | idb element-tree normalization accuracy | Proposal |

### Platform expansion (Android / Flutter)

The scope is currently **limited to the iOS Simulator** ([DESIGN §1](../../DESIGN.md)). This is the broad direction of going multi-platform by leveraging the driver / backend abstractions — a strategic decision that entails updating the core scope statement. The concrete approach and design are detailed in [multi-platform.md](../multi-platform.md).

| ID | Item | Status |
|---|---|---|
| [BE-0007](BE-0007-android-backend/BE-0007-android-backend.md) | Android backend | Proposal |
| [BE-0008](BE-0008-flutter-support/BE-0008-flutter-support.md) | Flutter support | Proposal |
| [BE-0009](BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) | Cross-platform abstractions | Proposal |
| [BE-0010](BE-0010-update-scope-statement/BE-0010-update-scope-statement.md) | Update the scope statement | Proposal |

### Authoring experience (record / GUI editor)

| ID | Item | Status |
|---|---|---|
| [BE-0012](BE-0012-action-capture-record/BE-0012-action-capture-record.md) | Action-capture record | Proposal |
| [BE-0013](BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) | Scenario GUI editor | Proposal |
| [BE-0014](BE-0014-record-demarcation/BE-0014-record-demarcation.md) | Demarcation from the existing AI record | Proposal |
| [BE-0015](BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) | Public hosting of the web UI | Proposal |
| [BE-0016](BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) | Self-hosting of the web UI | Proposal |

### Integration & automation (MCP)

| ID | Item | Status |
|---|---|---|
| [BE-0017](BE-0017-mcp-server/BE-0017-mcp-server.md) | MCP server | Proposal |
| [BE-0018](BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources.md) | Return evidence as MCP resources | Proposal |

### Backend expansion (iOS actuators)

| ID | Item | Status |
|---|---|---|
| [BE-0019](BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) | XCUITest backend | Proposal |
| [BE-0020](BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md) | Multi-backend evidence fallback | Proposal |

### doctor / onboarding

| ID | Item | Status |
|---|---|---|
| [BE-0024](BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md) | doctor / onboarding | Proposal |

### codegen coverage

| ID | Item | Status |
|---|---|---|
| [BE-0025](BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation.md) | Coordinate swipe generation | Proposal |
| [BE-0026](BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) | Shrink unsupported syntax | Proposal |

### Miscellaneous / on hold

| ID | Item | Status |
|---|---|---|
| [BE-0027](BE-0027-mock-server-external/BE-0027-mock-server-external.md) | `mockServer` (external mock) | Deferred |
| [BE-0028](BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard.md) | Guard against over-matching evidence rules | Proposal |

### Candidates from competitive research (MagicPod / Autify)

| ID | Item | Status | Origin |
|---|---|---|---|
| [BE-0029](BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md) | Visual-regression assertions | Proposal | Both |
| [BE-0035](BE-0035-device-control-primitives/BE-0035-device-control-primitives.md) | Extended device-control primitives | Proposal | MagicPod |
| [BE-0036](BE-0036-utility-steps/BE-0036-utility-steps.md) | Utility steps | Proposal | MagicPod |
| [BE-0037](BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md) | WebView / hybrid support | Proposal | MagicPod |
| [BE-0038](BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) | Autonomous crawl exploration (App Explorer style) | Proposal | Autify VAX |
| [BE-0040](BE-0040-ai-assertions/BE-0040-ai-assertions.md) | AI assertions | Deferred | MagicPod |

## Not adopting (already covered / out of scope)

- **Change history / version management** — already covered, since scenarios are YAML under git.
- **Cloud device farm / real-device / cloud execution** — out of the current iOS-Simulator-only scope ([DESIGN §1](../../DESIGN.md)). Multi-platform is tracked as proposals (the *Platform expansion* items).
- **Per-step screenshots / UI tree on error / device logs** — already covered by the evidence subsystem (capturePolicy + the `result:error` safety net).
- **NL→test generation (Autopilot equivalent)** — overlaps with the existing `record` + the *Authoring experience* items.
- **Scheduling / Slack / TestRail integration** — the domain of the CI / notification layer. Low priority (separately, if needed).
- **Automatic retry of failed tests** — in tension with determinism-first (no fixed sleeps, condition waits). It can hide flakiness, so if adopted at all it should be limited to quarantine use and needs careful consideration.

---

## Unsorted ideas

> Drop half-formed thoughts here. Promote them into a numbered BE item later.

-
