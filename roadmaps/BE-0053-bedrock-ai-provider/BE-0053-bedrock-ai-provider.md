**English** · [日本語](BE-0053-bedrock-ai-provider-ja.md)

# BE-0053 — Amazon Bedrock as a pluggable AI provider

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0053](BE-0053-bedrock-ai-provider.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0053") |
| Implementing PR | [#109](https://github.com/bajutsu-e2e/bajutsu/pull/109) |
| Topic | AI provider configuration |
<!-- /BE-METADATA -->

## Introduction

Let Bajutsu's Tier-1 AI paths (`record`, `triage`, `--dismiss-alerts`, `crawl`) talk to Claude
through **Amazon Bedrock** as an alternative to the direct Anthropic API. On the Bedrock path the
model calls authenticate with **AWS credentials (IAM)** instead of an `ANTHROPIC_API_KEY`. The
`anthropic` SDK already ships an `AnthropicBedrock` client whose `.messages.create()` surface is
identical to the default `Anthropic` client, so the prompt code at the call sites does not change;
what is needed is a small **provider seam** (one client factory + config) and **per-provider model
IDs**. Anthropic stays the default. This is the first concrete realization of the "provider is
pluggable" guarantee in [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md),
scoped to one provider. It stays strictly on the Tier-1 side: `run` and the CI gate call no model
and are unaffected ([DESIGN §2 / §3.1](../../DESIGN.md)).

## Motivation

Teams already standardized on AWS want their Claude usage to go through their own AWS account
rather than the Anthropic consumer API: no separate `ANTHROPIC_API_KEY` to provision and rotate,
authentication via the IAM roles / SSO they already run, usage billed on the AWS invoice through
Bedrock, and inference kept in a chosen AWS region under their existing AWS agreements. For such
teams the API-key requirement is friction, sometimes a hard procurement or data-residency blocker.

A common misconception is worth correcting up front, because it shapes the design: "Bedrock needs
no API key" does **not** mean "no authentication". Bedrock swaps the Anthropic key for **AWS
credentials** — environment variables, a shared profile, or an instance/role credential (and on
EC2/ECS/Lambda an IAM role means no secret is stored at all), with an optional Bedrock **bearer
token** (`AWS_BEARER_TOKEN_BEDROCK`) for environments that don't manage AWS credentials. So the
feature is "authenticate with AWS instead of an Anthropic key", not "credential-free".

Today this is impossible without code changes. All five AI entry points lazily construct
`anthropic.Anthropic()` in their `_ensure_client()` and read `ANTHROPIC_API_KEY` from the
environment, and each hardcodes a `claude-opus-4-8` model constant. There is no seam to point them
at Bedrock and no way to supply the Bedrock-form model ID.

Relationship to [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md): that
item is the broad trust/positioning guarantee (provider pluggability **plus** redaction on AI
inputs **plus** fail-closed-without-a-key). This item delivers one concrete provider and the
minimal seam it needs; BE-0047's redaction and fail-closed guarantees remain its own scope. The
two are complementary — this is the concrete provider that proves BE-0047's first guarantee.

## Detailed design

Proposal altitude. Everything below builds on seams that already exist.

### A single client factory (the seam)

Each of the five AI classes builds its client the same way — `import anthropic; self._client =
anthropic.Anthropic()` inside `_ensure_client()`:
`bajutsu/claude_agent.py`, `bajutsu/alerts.py`, `bajutsu/claude_triage.py`,
`bajutsu/crawl_guide.py`, `bajutsu/crawl_tabs.py`. `bajutsu/agents.py` already has a `make_agent`
factory for the record/crawl agent *kind*. Introduce one client factory (e.g.
`make_anthropic_client(config)`) that returns either `anthropic.Anthropic()` (today's `anthropic`
provider) or `anthropic.AnthropicBedrock(aws_region=…)` (the `bedrock` provider) from config, and
route all five `_ensure_client()` sites through it. Because both clients expose the same
`.messages.create()` — including prompt caching (`cache_control: ephemeral`) and base64 image
input, both of which Bedrock supports — the request bodies are unchanged. The injectable `client`
constructor param every class already accepts (used for testing) stays as the test seam.

### Config — app-agnostic provider selection

Add an AI-provider block under `defaults` / `apps.<name>` in `bajutsu/config.py`, resolved
`defaults < app` like the rest of the config ([DESIGN §8](../../DESIGN.md)):

- a provider selector — `anthropic` (default) or `bedrock`;
- for `bedrock`: `aws_region` (falls back to `AWS_REGION`), and optional credential/endpoint knobs;
- model IDs per provider (see below).

This keeps Anthropic the default — existing users see no change — and matches the app-agnostic
principle: per-provider differences live in config; the tool, drivers, and runner stay unchanged.
It is the config formalization BE-0047 calls for ("endpoint, model, key source explicit and
swappable"), realized for one provider.

> **Naming:** call this axis the **AI provider**, never "backend". In Bajutsu `backend:` already
> means the UI *actuator* (idb / future XCUITest / Web — [DESIGN §5 / §8](../../DESIGN.md),
> [BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)).
> The LLM provider is an orthogonal axis; overloading `backend` would conflate device actuation
> with model routing.

### Model IDs become provider-scoped

The hardcoded `claude-opus-4-8` constants in the five modules must be overridable per provider,
because Bedrock requires prefixed IDs: a global-endpoint form (`global.anthropic.…`, dynamic
routing, no premium) or a regional CRIS form (`us.anthropic.…`, guaranteed region, ~10% premium).
Each AI class already takes an injectable `model` param; surface it through the provider config so
a Bedrock run uses the Bedrock-form ID and an Anthropic run uses the bare ID.

### Authentication

`AnthropicBedrock` resolves AWS credentials through the standard provider chain —
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (plus `AWS_SESSION_TOKEN` for temporary credentials),
shared `~/.aws/credentials` profiles, or instance/role credentials. Region comes from the
`aws_region` argument or `AWS_REGION` (the SDK does **not** read `~/.aws/config` for region;
default `us-east-1`). A Bedrock bearer token (`AWS_BEARER_TOKEN_BEDROCK`) is also accepted. No
`ANTHROPIC_API_KEY` is required on this path. This dovetails with BE-0047's fail-closed rule: the
configured provider's credentials must resolve, with no silent fallback to another provider.

### Dependency

Bedrock support needs the `anthropic[bedrock]` extra (pulls in boto3/botocore for SigV4 signing),
which means a `uv.lock` update. To avoid forcing boto3 on users who never touch Bedrock, prefer an
**optional dependency group installed on demand** — mirroring how `make serve`
([scripts/serve.sh](../../scripts/serve.sh)) installs the idb backend's deps on demand — rather
than a hard core dependency. The exact packaging (optional extra vs. lazy install) is **TBD**.

### Feature support — no regression for the current AI paths

Used by Bajutsu and supported on Bedrock: Messages API, prompt caching (`cache_control`), base64
image input (the vision used by `alerts` / `crawl_tabs`), tool use, structured outputs.
**Not** on Bedrock but **not used** by Bajutsu's AI path today: the Files API and URL image
sources, server-side tools (code execution / web search), Batches, and the server-side `fallbacks`
parameter. So the existing AI paths port without a feature regression. Token accounting in
`bajutsu/usage.py` reads the same usage shape from Bedrock responses, so the usage report works
unchanged (cost itself lands on the AWS bill). The separate `claude-code` agent
(`bajutsu/claude_code_agent.py`, which shells out to the `claude` CLI and strips
`ANTHROPIC_API_KEY`) is **out of scope** here — that path has its own Bedrock mechanism.

### Open question — the exact Opus 4.8 Bedrock model ID

The default model is `claude-opus-4-8`. Bedrock's published model table tops out at Opus 4.6
(`global.anthropic.claude-opus-4-6-v1`); Opus 4.8 / 4.7 are reachable on `bedrock-runtime` but are
noted as lacking ARN-versioned IDs, so the **exact** Bedrock model-ID string for Opus 4.8 (and
whether it is served via the newer "Claude in Amazon Bedrock" Messages endpoint) must be confirmed
at implementation time against the target region's enabled model access. Because the model ID is a
config value, this is configuration, not a code change.

### doctor (optional)

`doctor` could gain a deterministic check that the configured provider's credentials resolve (AWS
credentials present / region set, or `ANTHROPIC_API_KEY` set for the Anthropic provider) and that
the configured model ID is well-formed for the provider — mirroring the existing executability
gate ([DESIGN §7.2](../../DESIGN.md)). **TBD** whether to include it in this item.

## Alternatives considered

- **Fold this into [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
  instead of a new item.** Rejected: BE-0047 is the broad guarantee (provider pluggability +
  redaction + fail-closed); Bedrock is a concrete, independently shippable provider integration
  with its own AWS-specific surface (auth chain, model-ID prefixes, dependency, region). Keeping it
  separate lets it land on its own while BE-0047 stays the umbrella it realizes.
- **Reuse the existing `backend` axis.** Rejected: `backend` is the UI actuator (idb / XCUITest /
  Web); the model provider is orthogonal. Overloading it would conflate device actuation with model
  routing. A distinct "AI provider" config key keeps the two axes clean.
- **Call Bedrock through boto3 / `InvokeModel` directly.** Rejected: `AnthropicBedrock` preserves
  the exact `.messages.create()` surface (caching, vision, tools) the five sites already use, so
  the change is a client swap behind a factory, not a rewrite of every prompt path.
- **Ship a hosted/default AWS credential for convenience.** Rejected (same spirit as BE-0047):
  credentials stay the user's; their absence is a clear error, not a silent fallback to someone
  else's account.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/agents.py`, `bajutsu/claude_agent.py`, `bajutsu/alerts.py`, `bajutsu/claude_triage.py`,
`bajutsu/crawl_guide.py`, `bajutsu/crawl_tabs.py`, `bajutsu/config.py`, `bajutsu/usage.py`,
`bajutsu/claude_code_agent.py`, [DESIGN §2 / §3.1 / §5 / §8](../../DESIGN.md),
[BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md),
[BE-0042 — Platform-aware backend registry](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md),
Anthropic docs — "Claude on Amazon Bedrock" (`AnthropicBedrock`, `anthropic.`-prefixed model IDs).
