**English** · [日本語](BE-0101-ai-free-zero-config-ja.md)

# BE-0101 — Legible Claude-using / Claude-free split with a zero-config non-AI path

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0101](BE-0101-ai-free-zero-config.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0101") |
| Implementing PR | [#432](https://github.com/bajutsu-e2e/bajutsu/pull/432) |
| Topic | AI provider configuration |
| Related | [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md), [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md), [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md) |
<!-- /BE-METADATA -->

## Introduction

Make the boundary between the features that **use Claude** and those that do not a **first-class,
legible property of the tool**, and guarantee that everything on the Claude-free side runs **with
zero configuration** — no credential, no `.env`, no setup of any kind. A new user should be able to
clone the repo and run `run` / `doctor` / `codegen` / `trace` / `lint` / `schema` (and the
deterministic parts of `serve`) immediately, while the Claude paths (`record` / `crawl` /
`triage --ai` / `run --dismiss-alerts`) are clearly marked as the ones that reach a model.

The axis is **whether Claude is invoked at all**, not whether a particular credential is present.
Claude can be reached by more than one provider and more than one agent backend — the Anthropic API
(`ANTHROPIC_API_KEY`), Amazon Bedrock (AWS credentials, [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)),
or the Claude Code CLI under a subscription login (`--agent claude-code`). An "API key" is only one
of those mechanisms; the property worth surfacing is the model call itself. "Claude-free" therefore
means no Claude at all, and so no AI credential, login, or runtime of any kind is needed.

This is a discoverability and developer-experience layer on top of the boundary that already exists
by design. It changes nothing about the prime directives: the Claude/Claude-free split it surfaces
*is* the Tier-1 / Tier-2 split, and nothing here puts a model on the `run` / CI gate.

## Motivation

Bajutsu's architecture already draws a hard line: the deterministic `run` / CI gate calls no model,
and only the Tier-1 authoring / investigation paths reach Claude. [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
turned the AI side into an *enforced* promise — the AI entry points fail closed when the resolved
provider has no usable credential (`anthropic_client.credential_gap()`), and [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)
made the provider pluggable. So the *plumbing* and the *enforcement* are done.

What is missing is the **user-facing legibility of the boundary** and an **explicit guarantee that
the Claude-free side needs nothing at all**. Three concrete gaps remain:

1. **The split is not discoverable.** There is no single place — neither documentation nor a
   machine-readable definition — that tells a user which commands invoke Claude and which do not.
   The fact lives in `CLAUDE.md` prose and in scattered call sites, so a newcomer cannot tell at a
   glance that `run` is Claude-free while `record` is not. The very property that most
   differentiates Bajutsu from vendor-cloud tools (you can adopt the deterministic core without ever
   touching a model) is invisible at the point of first contact.

2. **"API key" is the wrong unit; the real one is "uses Claude".** Framing the line as
   "needs a key" is too narrow and already inaccurate: the Bedrock provider authenticates with AWS
   credentials, not a key, and the `--agent claude-code` backend reaches Claude through the Claude
   Code CLI's own subscription login. The honest, stable axis is whether a path invokes Claude *at
   all*; the credential mechanism (Anthropic key, AWS creds, CLI login) is a detail of *how*, not
   *whether*. The classification and every surface built on it must be expressed on the "uses
   Claude" axis, so it stays correct as providers and agent backends are added.

3. **"Zero-config" is true but unguaranteed, and `serve` does not degrade gracefully.** The
   Claude-free path *does* run with no AI setup today, but nothing tests or pins that — a future
   top-level import of an SDK, a config default that reads an AI env var, or a `serve` startup path
   that probes for availability could quietly make the deterministic path require setup, with no
   gate to catch it. And the web UI presents the Claude authoring surfaces (`record` / `crawl`) and
   AI triage regardless of whether Claude is reachable; without it, a user discovers the requirement
   only by clicking and hitting a failure, with no up-front "this needs Claude" affordance.

Closing these turns "you can use Bajutsu without Claude" from an accident of the architecture into a
stated, tested, and visible property — the natural companion to BE-0047's "your AI, your key, your
data" on the other side of the same line.

## Detailed design

Proposal altitude. Four pieces, each building on a seam that already exists
(`anthropic_client.credential_gap()`, the agent-backend registry in `agents.py`, the per-command
CLI structure, the `serve` API-key endpoints). The work is MECE along these four pieces.

### 1. A single capability classification, on the "uses Claude" axis

Introduce one authoritative classification of every command / feature as **Claude-using** or
**Claude-free**, expressed once and consumed everywhere:

- A machine-readable definition co-located with the CLI command registry — each command carries a
  flag (e.g. `uses_claude: bool`) so the classification is data, not prose duplicated across docs.
  The Claude-using set is exactly `record`, `crawl`, `triage --ai`, and `run --dismiss-alerts`; the
  Claude-free set is everything else (`run`, `doctor`, `codegen`, `trace`, `lint`, `schema`,
  `approve`, `mcp`, `worker`, and the deterministic parts of `serve`). The flag is at the
  granularity of the *path*, not the command name: `triage` without `--ai` is Claude-free (the
  rule-based `HeuristicTriageAgent`), `triage --ai` is Claude-using, and a single flag flips it.
- The classification is **independent of provider and agent backend.** A command is Claude-using if
  it invokes a model on *any* provider (Anthropic, Bedrock) through *any* backend (the SDK agent or
  the Claude Code CLI). Which mechanism authenticates is out of scope for the *classification* (it
  is config, per BE-0047 / BE-0053); the classification answers only "does this reach Claude".
- A documentation page (bilingual, `docs/` + `docs/ja/`) that renders this split for humans — the
  canonical "what uses Claude, what doesn't" reference — and a short pointer from the getting-started
  material so it is the first thing a newcomer sees.
- The same data feeds the CLI help and `doctor` surfaces below, so there is exactly one source of
  truth and the three surfaces can never disagree.

### 2. A unified "is Claude reachable" check across providers and backends

`credential_gap()` answers availability for the **SDK** path (Anthropic key, or a Bedrock model id),
but the `--agent claude-code` backend reaches Claude through the CLI and has a different
availability condition (the `claude` binary present and its subscription logged in). Introduce one
resolver — `ai_availability(effective)` — that, given the resolved agent backend and provider,
returns a uniform answer ("reachable" or a specific, actionable gap: missing key, missing Bedrock
model, missing/unauthenticated CLI). The serve and doctor surfaces below gate on this one helper, so
they stay correct whichever backend/provider a target selects. This is a thin generalization of the
existing seam, not a new subsystem.

### 3. A tested zero-config guarantee for the Claude-free path

Pin the "Claude-free side needs nothing" promise with the same rigor BE-0047 gave the AI side, all
in the fast Linux gate (no device, no real API):

- **A regression test that runs the Claude-free commands with no AI setup present** (no
  `ANTHROPIC_API_KEY`, no AWS credentials, no `.env`, no `claude` CLI) and asserts they neither
  error on a missing credential nor construct any agent. Driven off the classification from piece 1,
  so the test covers the Claude-free set by construction and a newly added Claude-free command is
  automatically included.
- **An import-time guard:** assert that importing the modules on the deterministic path does not
  import an AI SDK or read an AI env var at module top level — the model client is constructed
  lazily, only inside the Claude entry points. This catches the most likely way zero-config silently
  regresses (a stray top-level `import anthropic` or `os.environ["ANTHROPIC_API_KEY"]`).
- These tests make the guarantee executable: "green locally" now includes "the Claude-free path is
  zero-config", and a change that breaks it fails the gate.

### 4. Graceful degradation in `serve`, and surfacing the split in CLI help and `doctor`

Make the boundary visible at the three points a user meets it:

- **`serve`** degrades rather than fails on use: when Claude is not reachable (piece 2's
  `ai_availability` against the resolved backend/provider), the Claude tabs / actions (`record`,
  `crawl`, AI triage) are shown **disabled**, with a clear inline explanation naming what is missing
  ("This needs Claude — set an API key, configure Bedrock, or sign in to the Claude Code CLI") and a
  pointer to the existing in-UI key field (`set_api_key`). The rest of the UI — run, reports,
  evidence, visual baselines, the GUI editor, `doctor` — stays fully interactive. A small status
  field (extending `api_key_info`) reports Claude availability so the front end gates on data, and
  flips live when Claude becomes reachable.
- **CLI help** groups commands (or annotates each) as *Claude-free* vs *uses Claude*, driven by the
  `uses_claude` data from piece 1 — so `bajutsu --help` itself shows the boundary.
- **`doctor`** reports **Claude readiness as a separate, clearly optional section**, independent of
  the device / convention readiness it already grades. A user with no AI setup sees their
  environment graded `Ready` for the deterministic path, with Claude shown as a distinct
  "not configured (optional)" line — never conflated with a blocking problem. This extends the
  [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md) onboarding surface and stays
  deterministic and LLM-free (it only inspects `ai_availability`).

### Prime-directive compliance

Everything here lives on, or describes, the Tier-1 side. The classification *names* the Claude
paths; it never adds one. `run` and the CI gate are unchanged — still no model, still machine-only
pass/fail. The `serve` and `doctor` changes only read `ai_availability` and gate UI / reporting on
it; they are deterministic and add no LLM call. App-specific differences (which provider, which
backend, which credential) continue to come from config (the `ai` block from BE-0047), so the tool
stays app-agnostic.

## Alternatives considered

- **Leave it implicit (status quo).** Rejected for the same reason BE-0047 rejected leaving the
  AI-side guarantee implicit: the property that most distinguishes Bajutsu — adoptable without
  Claude — is invisible to a prospective user and unprotected against regression. An emergent
  property is not a feature you can point to.
- **Frame the line as "needs an API key".** Rejected as too narrow and already wrong: it ignores the
  Bedrock (AWS credentials) and Claude Code CLI (subscription login) paths to the same model. The
  "uses Claude" axis is the one that stays correct as providers and backends are added; the
  credential mechanism is a detail beneath it.
- **Fold it into BE-0024 (doctor / onboarding).** The `doctor` Claude-readiness piece does belong to
  that surface and will land there. But the classification map, the unified availability check, the
  zero-config test guarantee, and the `serve` degradation are cross-cutting and larger than the
  "small onboarding tweak" BE-0024 is scoped for; they warrant a dedicated item.
- **Hide Claude commands entirely when Claude is unreachable.** Rejected: hiding them makes the
  tool's full capability *less* discoverable, not more. The goal is a legible boundary — show the
  Claude features, clearly marked — not a smaller-looking tool.
- **Ship a bundled / convenience credential so everything "just works".** Rejected — it recreates
  exactly the vendor-cloud data-egress concern BE-0047 exists to avoid, and it dissolves the very
  boundary this item is trying to make visible.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Single capability classification — machine-readable `uses_claude` data + bilingual docs page
- [x] Unified `ai_availability` check across providers (Anthropic / Bedrock) and backends (SDK / Claude Code CLI)
- [x] Tested zero-config guarantee — no-AI-setup regression test + import-time guard
- [x] Surfacing — `serve` disabled-tab degradation, grouped CLI help, separate optional `doctor` Claude-readiness section

**Log**

- Shipped all four pieces in one change: `bajutsu/capabilities.py` (the single classification,
  consumed by `--help`, the docs page, `doctor`, and the zero-config test), `bajutsu/ai_availability.py`
  (unified reachability across the SDK and Claude Code CLI backends), `tests/test_zero_config.py`
  (import-time guard + no-AI-setup regression over the Claude-free set), and the surfacing — grouped
  `bajutsu --help`, the optional `doctor` "Claude (optional)" section, and `serve`'s disabled
  record / crawl tabs with an inline explanation. Docs: `docs/ai-boundary.md` (+ ja mirror).

## References

`bajutsu/anthropic_client.py` (`credential_gap` / `provider` / `make_client` — the SDK-path
availability seam), `bajutsu/agents.py` (`AGENT_KINDS = ("api", "claude-code")` — the agent-backend
registry), `bajutsu/claude_code_agent.py` (the subscription-login CLI backend), `bajutsu/cli/`
(per-command registry the `uses_claude` flag attaches to), `bajutsu/serve/` (`api_key_info` /
`set_api_key` — the key surface to gate on), `bajutsu/doctor.py` · `bajutsu/preflight.py` (the
onboarding surface), [DESIGN §2 / §3.1](../../DESIGN.md), [CLAUDE.md](../../CLAUDE.md) (the
prose statement of the boundary this item makes legible), [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
(the enforcement counterpart on the AI side), [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)
(pluggable provider), [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md) (the
doctor / onboarding surface the Claude-readiness section extends).
