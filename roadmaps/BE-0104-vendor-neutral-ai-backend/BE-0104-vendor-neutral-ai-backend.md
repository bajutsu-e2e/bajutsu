**English** · [日本語](BE-0104-vendor-neutral-ai-backend-ja.md)

# BE-0104 — Vendor-neutral AI backend interface

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0104](BE-0104-vendor-neutral-ai-backend.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0104") |
| Implementing PR | [#608](https://github.com/bajutsu-e2e/bajutsu/pull/608) |
| Topic | AI provider configuration |
| Related | [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md), [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md), [BE-0097](../BE-0097-crawl-ai-data-sovereignty/BE-0097-crawl-ai-data-sovereignty.md), [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md), [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend.md) |
<!-- /BE-METADATA -->

## Introduction

Apply Bajutsu's **backend-agnostic** philosophy — *a platform is a backend behind one interface* —
to the AI paths as well: **an AI provider is a backend behind one interface.** Introduce a
vendor-neutral seam that describes only what Bajutsu actually asks of a model (an agentic tool-use
turn, vision input, plain completion), so that the Claude-using features can be re-pointed at a
**different model family** — not merely a different Claude endpoint — without touching their call
sites.

This item is **design plus a single reference adapter (the existing Anthropic path)**. It ships no
second vendor; adding one (OpenAI-compatible endpoints, Google Gemini, a locally hosted OSS model)
is deliberately deferred to follow-up items that plug into the seam this one defines. The goal here
is to make "swap the AI" *possible and safe*, and to prove the seam with the provider already in
use.

Nothing here goes near the prime directives: the seam lives entirely on the Tier-1
authoring / investigation paths, and never on the deterministic `run` / CI gate.

## Motivation

[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) and
[BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md) already made
the *provider* pluggable — every AI path reaches a model through the single factory in
`anthropic_client.py`, and config chooses the Anthropic API, Amazon Bedrock, or a self-hosted
gateway. But that factory returns `Anthropic | AnthropicBedrock` — **both are Anthropic SDK
clients**, and the call sites (`claude_agent.py`, `claude_triage.py`, `alerts.py`,
`claude_enrich_agent.py`, `crawl_guide.py`, `crawl_tabs.py`) speak the Anthropic Messages API
directly: `tool_use` / `tool_result` content blocks, Anthropic's image-block shape for vision, its
system-prompt convention. Today's "swap" is therefore *which Claude endpoint*, not *which model
family*.

Wanting the deeper seam is not tied to one destination — the value is that the choice stays open on
several axes at once:

1. **Avoiding vendor lock-in.** The abstraction's payoff is that being able to substitute the model
   is itself the feature. A single-vendor coupling that reaches into every AI call site is a
   liability regardless of how good that vendor is; the architecture should not assume one forever.
2. **On-prem / data sovereignty.** BE-0047 made "your AI, your key, your data" an enforced promise,
   but only across Anthropic-shaped endpoints. Some users need to run a **local or self-hosted OSS
   model** (e.g. via Ollama or vLLM) so that screenshots and element trees never leave their
   environment at all — a model family the current seam cannot reach.
3. **Cost / lightweight models.** Different paths have very different demands (a quick alert-dismissal
   locator vs. a multi-turn record agent). A neutral seam lets an operator route cheaper or smaller
   models per path and per target, rather than being fixed to one vendor's lineup.
4. **Specific vendor capabilities.** A given model may have better vision accuracy, latency, or
   tool-calling behavior for a particular path. Being able to pick it — for that path only — is
   worth the abstraction even before any second adapter ships.

What blocks all four today is the same thing: the neutral shape doesn't exist. The call sites are
written against Anthropic types, so "add OpenAI" or "add a local model" means editing every AI path,
not registering an adapter. This item creates the shape and proves it with Anthropic, so the four
motivations above become follow-up adapters rather than cross-cutting rewrites.

A second, structural payoff: **redaction becomes a property of the seam.** Today
[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) /
[BE-0097](../BE-0097-crawl-ai-data-sovereignty/BE-0097-crawl-ai-data-sovereignty.md)
route inputs through `redaction.py` before they reach the Anthropic client. If every provider must
pass through one neutral interface, that interface is the single chokepoint where the
data-sovereignty guarantee is enforced — so any future adapter inherits it by construction, instead
of each vendor integration having to remember to redact.

## Detailed design

Proposal altitude. The seam is a thin capability layer, not a general LLM framework: it describes
*only* what Bajutsu's existing AI paths already use. Built on the seam that exists today
(`anthropic_client.make_client` / `resolve_model` / `credential_gap`, the per-path `MODEL`
constants, the `AiConfig` / `AiSettings` config blocks). The work is MECE along the seven pieces
below.

### 1. Capability audit — the minimal neutral surface

Enumerate exactly what each AI path asks of a model, and nothing more, so the interface stays
narrow:

- **Agentic tool-use turn** — `record` (`claude_agent.py`), `triage --ai` (`claude_triage.py`), MCP
  enrich (`claude_enrich_agent.py`), and the crawl guide (`crawl_guide.py`) run a multi-turn loop:
  send messages + tool definitions, receive text and/or tool-use requests, feed tool results back.
- **Vision input** — `run --dismiss-alerts` (`alerts.py`), `triage --ai`, and `record` attach
  screenshots as image content.
- **Plain / short structured completion** — `crawl_tabs.py` (tab identification) is essentially a
  single-shot classification.

The audit produces the definitive list of capabilities the neutral interface must express — and,
just as importantly, the list of Anthropic-specific behaviors that are *not* used and need not be
modeled.

### 2. The vendor-neutral interface

Define a small provider-neutral interface (a `Protocol`) plus normalized request / response types
that cover exactly the audit's surface:

- **Request**: system prompt, a list of normalized messages (text, image, tool-result parts), tool
  definitions (name / description / input schema), model id, `max_tokens`.
- **Response**: normalized content blocks — text and tool-use requests (name + arguments) — plus a
  stop reason sufficient to drive the tool loop.
- **Method(s)**: one `create_message(...)`-style call the loop can iterate on. No streaming (no
  current path needs it); the surface stays minimal.

The types are Bajutsu's own, deliberately *not* re-exports of Anthropic SDK types, so no call site
depends on a vendor's shape.

### 3. Provider registry — the extension point

Generalize the current two-value `provider` (`anthropic` / `bedrock`) into a **registry keyed by
provider name → adapter factory**. Adding a provider becomes "register an adapter", not "edit a
factory `if`-chain". This item ships the registry and documents the contract a new adapter must
satisfy (implement the piece-2 interface; declare its `credential_gap`); it does **not** register a
second vendor.

### 4. Anthropic reference adapter

Reimplement the existing behavior as the first adapter behind the piece-2 interface, wrapping
today's `make_client` (so **both** the Anthropic API and Amazon Bedrock stay covered — Bedrock is an
Anthropic-SDK variant and naturally lives inside this one adapter). This adapter translates the
neutral request into Anthropic Messages API calls and the Anthropic response back into neutral
content blocks. Behavior is unchanged; the model ids and `credential_gap` semantics from BE-0047 /
BE-0053 are preserved.

### 5. Migrate the call sites

Rewrite each AI path (`claude_agent.py`, `claude_triage.py`, `alerts.py`,
`claude_enrich_agent.py`, `crawl_guide.py`, `crawl_tabs.py`) to construct and use the neutral
interface instead of the `anthropic` client and its message/tool/image types. This is
behavior-preserving refactoring — the observable output of each path is unchanged — and it is the
step that actually removes the vendor coupling.

### 6. Config schema — an open provider

Keep `AiSettings` / `AiConfig` as they are, but make `provider` an **open, registry-validated**
value rather than the closed `anthropic | bedrock` pair. An unknown provider fails closed with a
clear error (the same fail-closed discipline BE-0047 established). The check lives in the AI layer,
not at config load: the deterministic core (`config`) must not import the AI provider registry
(BE-0112), so config accepts the name and the registry rejects an unregistered one the first time an
AI path resolves the provider. Document how a `provider` name maps to a registered adapter. No new
config *fields* are required for this item.

### 7. Contract tests

Add contract tests for the neutral interface using an **in-process fake adapter** (no network, no
device — runs in the fast Linux gate): assert the tool-use loop, vision input, and completion paths
drive the interface correctly, and that redaction runs before any adapter is reached. Assert the
Anthropic adapter still produces the same requests it does today (behavior-unchanged guarantee for
piece 5).

### Prime-directive compliance

The seam is used **only** on the Tier-1 authoring / investigation paths that already call a model;
it names and re-homes those calls, it never adds one. `run` and the CI gate stay model-free —
deterministic, machine-only pass/fail — and the neutral interface is never imported on that path
(consistent with [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md)'s
zero-config Claude-free guarantee). Provider choice remains config (`targets.<name>.ai`), so the
tool / drivers / runner stay app-agnostic. Determinism is untouched — no path gains a `sleep` or an
ambiguous selector.

## Alternatives considered

- **Adopt a third-party LLM abstraction (LiteLLM / LangChain / similar).** Rejected: they carry a
  large dependency and behavioral surface, their routing is opaque to the determinism and redaction
  guarantees Bajutsu must hold, and Bajutsu needs only a narrow capability surface (the piece-1
  audit). A small, owned interface is easier to reason about and to keep the redaction chokepoint
  honest than a general framework.
- **Rely on Anthropic-compatible gateways via `base_url` only.** The `base_url` override
  (BE-0053-era) already lets some proxies stand in front of the Anthropic API. Rejected as
  insufficient: it only reaches endpoints that speak the Anthropic Messages schema, so it cannot
  address OpenAI, Gemini, or most local runtimes whose message / tool / vision shapes differ. It
  solves "different endpoint", not "different model family" — the actual ask.
- **Support an OpenAI-compatible shim only.** Many providers and local runtimes expose an
  OpenAI-compatible API, so a single OpenAI-shaped adapter would cover a lot. Rejected as the
  *interface* choice: coupling the neutral seam to OpenAI's shape just trades one vendor lock-in for
  another and still can't express Anthropic's or Gemini's native shapes cleanly. An
  OpenAI-compatible adapter is a fine *follow-up* that plugs into the neutral registry — but the
  seam itself must be vendor-neutral.
- **Ship a second vendor now (bigger scope).** Rejected for this item by choice: proving the seam
  with the provider already in use keeps the change behavior-preserving and reviewable, and lets each
  real vendor land as its own focused, independently-testable adapter afterward.
- **Do nothing / keep Anthropic-only.** Rejected: it leaves all four motivations (lock-in, on-prem,
  cost, capability) blocked behind a cross-cutting rewrite, and leaves redaction enforced per-call
  site rather than at one seam.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Capability audit — the minimal neutral surface the AI paths require (tool-use loop / vision / completion)
- [x] Vendor-neutral interface — `Protocol` + normalized request / response types
- [x] Provider registry — name → adapter-factory extension point, with the adapter contract documented
- [x] Anthropic reference adapter — wraps `make_client` (Anthropic + Bedrock), behavior-unchanged
- [x] Migrate call sites — `claude_agent` / `claude_triage` / `alerts` / `claude_enrich_agent` / `crawl_guide` / `crawl_tabs` off direct `anthropic` types
- [x] Config schema — `provider` becomes open; the registry (AI layer) validates and fails closed on unknown, since the core can't import it (BE-0112)
- [x] Contract tests — fake-adapter interface tests + Anthropic behavior-unchanged assertions + redaction-before-adapter check

The capability audit found that every AI path is a *single-shot forced-tool* `create` call — no
path feeds `tool_result` blocks back (the `record` loop drives many fresh single turns, not one
multi-turn conversation), so the neutral surface is one `create_message` per turn and models no
tool-result parts. The seam lives in `bajutsu/ai/` (`base` = `AiBackend` protocol + normalized
types, `registry` = provider name → adapter, `anthropic` = the reference adapter over
`anthropic_client.make_client`, covering the Anthropic API and Amazon Bedrock). `credential_gap` is
now dispatched through the registry so a future adapter declares its own; `anthropic_client.provider`
stays as the Anthropic-family sub-provider selector. Redaction is preserved exactly per call site
(behavior-unchanged) rather than relocated into the seam, to keep `crawl_tabs`, which redacts
nothing today, unchanged.

Syncing this branch with `main` brought BE-0112's layer-boundary gate (import-linter), which forbids
the deterministic core from importing the periphery AI stack — and BE-0112 moved `AiConfig` into
`config`. The two items met at provider validation: the original design rejected an unknown provider
in `AiSettings` at config load, but that made `config` import `bajutsu.ai` (transitively reaching
`anthropic_client`), breaking the gate. The fail-closed check moved from config load into the
registry's provider resolution (`_provider_name` now raises on an unregistered name), so `config`
stops importing the AI stack and the guarantee is preserved, just at the first AI use rather than at
load ([#608](https://github.com/bajutsu-e2e/bajutsu/pull/608)).

## References

`bajutsu/anthropic_client.py` (`make_client` / `resolve_model` / `credential_gap` / `provider` /
`AiConfig` — the current single seam this item generalizes), the AI call sites
`bajutsu/claude_agent.py` · `bajutsu/claude_triage.py` · `bajutsu/alerts.py` ·
`bajutsu/claude_enrich_agent.py` · `bajutsu/crawl_guide.py` · `bajutsu/crawl_tabs.py` (the paths
that speak the Anthropic Messages API today), `bajutsu/config.py` (`AiSettings` / `Effective.ai` —
the config surface), `bajutsu/redaction.py` (the guarantee the neutral seam would enforce
uniformly), [DESIGN.md](../../../DESIGN.md) (the backend-agnostic Driver philosophy this mirrors on
the AI side), [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
(enforced provider-agnostic, redacted AI path), [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)
(Bedrock as the first pluggable provider — subsumed by the Anthropic adapter here),
[BE-0097](../BE-0097-crawl-ai-data-sovereignty/BE-0097-crawl-ai-data-sovereignty.md)
(redaction extended to crawl / serve AI paths),
[BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md) (the legible Claude-using /
Claude-free split, whose zero-config guarantee this item must not disturb).
