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

Bajutsu's Tier-1 AI paths (`record`, `triage`, `--dismiss-alerts`, and `crawl`) should reach Claude through **Amazon Bedrock** as an alternative to the direct Anthropic API. On the Bedrock path, the model calls authenticate with **AWS credentials (IAM)** instead of an `ANTHROPIC_API_KEY`.

The `anthropic` SDK already ships an `AnthropicBedrock` client. Its `.messages.create()` surface is identical to the default `Anthropic` client, so the prompt code at the call sites does not change. The work needs a small **provider seam** (one client factory plus config) and **per-provider model IDs**. Anthropic stays the default.

This item is the first concrete realization of the "provider is pluggable" guarantee in [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md), scoped to one provider. It stays on the Tier-1 side. `run` and the CI gate call no model and are unaffected ([DESIGN §2 / §3.1](../../DESIGN.md)).

## Motivation

Teams that have standardized on AWS want their Claude usage to go through their own AWS account instead of the Anthropic consumer API. They get four things:

- no separate `ANTHROPIC_API_KEY` to provision and rotate;
- authentication through the IAM roles and SSO they already run;
- usage billed on the AWS invoice through Bedrock;
- inference kept in a chosen AWS region under their existing AWS agreements.

For these teams the API-key requirement is friction. Sometimes it is a hard procurement or data-residency blocker.

One misconception shapes the design, so we correct it up front. "Bedrock needs no API key" does **not** mean "no authentication". Bedrock swaps the Anthropic key for **AWS credentials**. Those credentials come from environment variables, a shared profile, or an instance or role credential. On EC2, ECS, and Lambda, an IAM role means no secret is stored at all. Environments that do not manage AWS credentials can use an optional Bedrock **bearer token** (`AWS_BEARER_TOKEN_BEDROCK`). The feature is "authenticate with AWS instead of an Anthropic key", not "credential-free".

Today this is impossible without code changes. All five AI entry points lazily construct `anthropic.Anthropic()` in their `_ensure_client()` and read `ANTHROPIC_API_KEY` from the environment. Each also hardcodes a `claude-opus-4-8` model constant. No seam points them at Bedrock, and nothing supplies the Bedrock-form model ID.

[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) is the broad trust and positioning guarantee. It combines provider pluggability, redaction on AI inputs, and fail-closed behavior without a key. This item delivers one concrete provider and the minimal seam that provider needs. BE-0047 keeps its redaction and fail-closed guarantees as its own scope. The two items complement each other: this item is the concrete provider that proves BE-0047's first guarantee.

## Detailed design

This section works at proposal altitude. Everything below builds on seams that already exist.

### A single client factory (the seam)

Five AI classes build their client the same way. Each calls `import anthropic; self._client = anthropic.Anthropic()` inside `_ensure_client()`. They live in `bajutsu/claude_agent.py`, `bajutsu/alerts.py`, `bajutsu/claude_triage.py`, `bajutsu/crawl_guide.py`, and `bajutsu/crawl_tabs.py`. Separately, `bajutsu/agents.py` already has a `make_agent` factory for the record and crawl agent *kind*.

We introduce one client factory (for example `make_anthropic_client(config)`). From config, it returns either `anthropic.Anthropic()` (today's `anthropic` provider) or `anthropic.AnthropicBedrock(aws_region=…)` (the `bedrock` provider). All five `_ensure_client()` sites route through it.

Both clients expose the same `.messages.create()`. Bedrock supports prompt caching (`cache_control: ephemeral`) and base64 image input, so the request bodies stay unchanged. Every class already accepts an injectable `client` constructor param for testing. That param stays as the test seam.

### Config — app-agnostic provider selection

We add an AI-provider block under `defaults` and `apps.<name>` in `bajutsu/config.py`. It resolves `defaults < app` like the rest of the config ([DESIGN §8](../../DESIGN.md)). The block holds three things:

- a provider selector, `anthropic` (default) or `bedrock`;
- for `bedrock`, `aws_region` (falling back to `AWS_REGION`) and optional credential and endpoint knobs;
- model IDs per provider (see below).

Anthropic stays the default, so existing users see no change. The block also matches the app-agnostic principle: per-provider differences live in config, and the tool, drivers, and runner stay unchanged. It formalizes the config that BE-0047 calls for ("endpoint, model, key source explicit and swappable") for one provider.

> **Naming:** call this axis the **AI provider**, never "backend". In Bajutsu, `backend:` already means the UI *actuator* (idb, a future XCUITest, or Web — [DESIGN §5 / §8](../../DESIGN.md), [BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)). The LLM provider is an orthogonal axis. Overloading `backend` would conflate device actuation with model routing.

### Model IDs become provider-scoped

Each provider needs its own override of the hardcoded `claude-opus-4-8` constants in the five modules. Bedrock requires prefixed IDs in one of two forms:

- a global-endpoint form (`global.anthropic.…`) with dynamic routing and no premium;
- a regional cross-region inference (CRIS) form (`us.anthropic.…`) with a guaranteed region and a premium of about 10%.

Each AI class already takes an injectable `model` param. We surface that param through the provider config. A Bedrock run then uses the Bedrock-form ID, and an Anthropic run uses the bare ID.

### Authentication

`AnthropicBedrock` resolves AWS credentials through the standard provider chain. The sources are `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (plus `AWS_SESSION_TOKEN` for temporary credentials), shared `~/.aws/credentials` profiles, and instance or role credentials.

The region comes from the `aws_region` argument or `AWS_REGION`. The SDK does **not** read `~/.aws/config` for the region, and the default is `us-east-1`. A Bedrock bearer token (`AWS_BEARER_TOKEN_BEDROCK`) is also accepted. This path needs no `ANTHROPIC_API_KEY`.

The design dovetails with BE-0047's fail-closed rule. The configured provider's credentials must resolve, with no silent fallback to another provider.

### Dependency

Bedrock support needs the `anthropic[bedrock]` extra, which pulls in boto3 and botocore for SigV4 signing. The extra means a `uv.lock` update. To avoid forcing boto3 on users who never touch Bedrock, we prefer an **optional dependency group installed on demand** over a hard core dependency. `make serve` ([scripts/serve.sh](../../scripts/serve.sh)) already installs the idb backend's deps on demand the same way. The exact packaging (optional extra or lazy install) is **TBD**.

### Feature support — no regression for the current AI paths

Bajutsu uses these features, and Bedrock supports them: the Messages API, prompt caching (`cache_control`), base64 image input (the vision that `alerts` and `crawl_tabs` use), tool use, and structured outputs.

Bedrock lacks these features, and Bajutsu's AI path does not use them today: the Files API and URL image sources, server-side tools (code execution and web search), Batches, and the server-side `fallbacks` parameter. The existing AI paths therefore port without a feature regression.

Token accounting in `bajutsu/usage.py` reads the same usage shape from Bedrock responses, so the usage report works unchanged. The cost itself lands on the AWS bill.

The separate `claude-code` agent (`bajutsu/claude_code_agent.py`) is **out of scope** here. It shells out to the `claude` CLI and strips `ANTHROPIC_API_KEY`, and that path has its own Bedrock mechanism.

### Open question — the exact Opus 4.8 Bedrock model ID

The default model is `claude-opus-4-8`. Bedrock's published model table tops out at Opus 4.6 (`global.anthropic.claude-opus-4-6-v1`). Opus 4.8 and 4.7 are reachable on `bedrock-runtime`, but they are noted as lacking ARN-versioned IDs.

Implementation time must confirm two points against the target region's enabled model access. One is the **exact** Bedrock model-ID string for Opus 4.8. The other is whether the newer "Claude in Amazon Bedrock" Messages endpoint serves it. The model ID is a config value, so the answer is a configuration matter, not a code change.

### doctor (optional)

`doctor` could gain a deterministic check of two things. First, the configured provider's credentials resolve: AWS credentials are present and the region is set, or `ANTHROPIC_API_KEY` is set for the Anthropic provider. Second, the configured model ID is well-formed for the provider. The check would mirror the existing executability gate ([DESIGN §7.2](../../DESIGN.md)). Whether to include it in this item is **TBD**.

## Alternatives considered

- **Fold this into [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) instead of a new item.** Rejected. BE-0047 is the broad guarantee: provider pluggability, redaction, and fail-closed behavior. Bedrock is a concrete provider integration that ships independently. It has its own AWS-specific surface: the auth chain, model-ID prefixes, the dependency, and the region. Keeping it separate lets it land on its own, while BE-0047 stays the umbrella that it realizes.
- **Reuse the existing `backend` axis.** Rejected. `backend` is the UI actuator (idb, XCUITest, or Web), and the model provider is orthogonal. Overloading `backend` would conflate device actuation with model routing. A distinct "AI provider" config key keeps the two axes clean.
- **Call Bedrock through boto3 or `InvokeModel` directly.** Rejected. `AnthropicBedrock` preserves the exact `.messages.create()` surface (caching, vision, tools) that the five sites already use. The change is then a client swap behind a factory, not a rewrite of every prompt path.
- **Ship a hosted or default AWS credential for convenience.** Rejected, in the same spirit as BE-0047. Credentials stay the user's. Their absence is a clear error, never a silent fallback to someone else's account.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/agents.py`, `bajutsu/claude_agent.py`, `bajutsu/alerts.py`, `bajutsu/claude_triage.py`, `bajutsu/crawl_guide.py`, `bajutsu/crawl_tabs.py`, `bajutsu/config.py`, `bajutsu/usage.py`, `bajutsu/claude_code_agent.py`, [DESIGN §2 / §3.1 / §5 / §8](../../DESIGN.md), [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md), [BE-0042 — Platform-aware backend registry](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md), and the Anthropic docs "Claude on Amazon Bedrock" (`AnthropicBedrock`, `anthropic.`-prefixed model IDs).
