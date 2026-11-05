**English** · [日本語](BE-0047-ai-data-sovereignty-ja.md)

# BE-0047 — AI data sovereignty (provider-agnostic, redacted AI path)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0047](BE-0047-ai-data-sovereignty.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0047") |
| Implementing PR | [#356](https://github.com/bajutsu-e2e/bajutsu/pull/356) |
| Topic | AI provider configuration |
| Related | [BE-0394](../BE-0394-ai-provider-none-kill-switch/BE-0394-ai-provider-none-kill-switch.md) |
| Origin | Maestro |
<!-- /BE-METADATA -->

## Introduction

This item makes three guarantees explicit and enforced. First, Bajutsu's AI paths (`record`,
`triage`, `--dismiss-alerts`) run under the user's own API key and provider. Second, everything sent
to the model passes through the existing redaction layer first. Third, the provider behind the
authoring / investigation agent is pluggable. The item is a trust and data-handling guarantee, not a
runtime check. The gate stays AI-free regardless.

## Motivation

A concrete 2026 competitive shift sharpened this. Maestro moved its AI features to a
**Cloud-managed** model. Bring-your-own-key was removed (`MAESTRO_CLI_AI_KEY` /
`MAESTRO_CLI_AI_MODEL` deprecated). AI commands now route through Maestro Cloud over opaque
"third-party AI providers", and using them requires a Maestro account. Screenshots of the app under
test go to a vendor service that the user does not control. For regulated industries and
privacy-sensitive teams, that is a hard adoption blocker.

Bajutsu is already the opposite. Its AI paths use the user's own `ANTHROPIC_API_KEY` and run
locally, and the deterministic gate calls no model at all. That is today an **emergent
property**, not a documented and enforced promise. A buyer cannot point to a guarantee, and one part
is genuinely unenforced. Redaction is applied to written *evidence*. Nothing guarantees, though,
that the screenshots and element trees sent to the model during `record` / `triage` /
`--dismiss-alerts` are redacted first.

This item turns "your AI, your key, your data" from an accident into a first-class, verifiable
property. It is a direct contrast with vendor-cloud AI.

## Detailed design

This section works at proposal altitude. It describes three guarantees, each building on a seam that
already exists.

- **Provider is pluggable.** The `Agent` / `claude_agent` abstraction
  (`bajutsu/agent.py`, `bajutsu/claude_agent.py`) already isolates the model call. A formal provider config (under
  `defaults` / `targets.<name>`) makes the endpoint, model, and key source explicit and swappable.
  For example, the authoring / triage agent can point at a self-hosted gateway or an enterprise
  proxy. Claude (Anthropic) stays the default, and nothing here weakens that.
- **Redaction on AI inputs.** The screenshots, element trees, and logs handed to `record` / `triage`
  / `--dismiss-alerts` pass through `bajutsu/redaction.py` *before they leave the process*, and the
  item documents the guarantee. This closes the gap between "evidence is redacted" and "what the model
  sees is redacted".
- **No key, no AI (fail closed).** The AI paths require an explicitly configured key and never
  silently fall back to a hosted default. Absence of a key is a clear error, not a quiet round-trip
  to someone else's cloud.

This item stays strictly on the Tier-1 side. It changes nothing about `run`: pass/fail remains
machine-only, and no model is added to the gate. It is a trust / positioning feature with concrete
enforcement, not a new kind of check.

The implementation-level design of each guarantee follows.

### Provider configuration

Today the provider is **env-only** (`bajutsu/anthropic_client.py`: `BAJUTSU_AI_PROVIDER`,
`ANTHROPIC_API_KEY`, `BAJUTSU_BEDROCK_MODEL`, the AWS chain). The design formalizes it as an
optional `ai` block, resolved like any other setting: `defaults.ai`, overridable per
`targets.<name>.ai`.

```yaml
defaults:
  ai:
    provider: anthropic                      # anthropic (default) | bedrock
    model: claude-opus-4-8                    # optional: override the path's default model
    baseUrl: https://ai-gateway.internal/v1  # optional: self-hosted gateway / enterprise proxy (anthropic)
    keyEnv: ANTHROPIC_API_KEY                 # the NAME of the env var holding the key — never the key
```

- **Keys never live in config.** `keyEnv` names an environment variable, and the value is read from the
  environment at call time. A secret never lands in the repo or in an
  uploaded bundle (CLAUDE.md). `baseUrl` points the Anthropic SDK at a self-hosted gateway / proxy
  (`Anthropic(base_url=…, api_key=os.environ[keyEnv])`). Bedrock keeps the AWS credential chain.
- **Config first, env fallback.** `provider()` / `make_client()` / `resolve_model()` form the single
  factory that every AI path already shares. They read the resolved `ai` config first and fall back
  to today's env vars, so existing setups keep working unchanged. The block resolves into
  `Effective`, so the CLI and `serve` agree on one source of truth.
- **App-agnostic.** The change is config, not code. The agent / locator call sites are untouched.

### Redaction on AI inputs and the screenshot caveat

The run-scoped `Redactor` (`bajutsu/redaction.py`) comes from the target's `redact` keys plus
resolved secret values, exactly as evidence writing already builds it in `bajutsu/evidence.py`. It
applies to the **textual** model inputs before they leave the process:

- **record** — `claude_agent._render` / `_user_content`: the screen's element list
  (`label` / `value` / `traits`) is `redact_elements`-ed before it is rendered into the user
  message.
- **triage** — `TriageContext.elements` and the failure text: redacted before the prompt is
  assembled.
- **free text / logs** handed to any AI path: `redact_text`. This includes `--dismiss-alerts`.
  That path sends a screenshot **plus** a small text block (the image dimensions and an optional,
  possibly user-supplied `--alert-instruction`). The instruction is run through `redact_text` too.

**The honest limit: screenshots are images, and `redaction.py` masks text and element trees, not
pixels.** record / triage / dismiss-alerts each send a screenshot, which redaction cannot scrub. The
guarantee is therefore two-part:

1. **Textual** inputs (element trees, logs) are redacted before send.
2. **Every** input, screenshots included, goes to the **user-configured provider/endpoint** alone,
   never a vendor default. A screenshot never crosses the user's trust boundary.

For teams that need the pixels off the wire too, an optional `ai.sendScreenshots: false` (text-only,
at an accuracy cost) is offered. Pixel-level image redaction is a separate, much larger effort and
stays out of scope.

### Fail closed

The AI entry points (`record`, `triage`, `--dismiss-alerts`) hard-fail with a clear,
provider-specific error when the selected provider has no usable credential
(`anthropic_client.credential_gap()` is non-None). They never construct a client that falls back to
a hosted default. This tightens the existing `crawl` / `run` gate-or-warn into a strict fail-closed
on the authoring / investigation paths. A missing key is an actionable error, not a quiet round-trip
to someone else's cloud.

### Validation

Everything is machine-checkable in the fast gate. No real API is needed, since the SDK client is
already injectable (`make_client(client=…)`). No test puts an LLM on the `run` / CI gate:

- **Provider resolution.** A recording fake client asserts that the endpoint / model / key-env
  resolve from the `ai` config, with config-over-env precedence and the env fallback intact.
- **Redaction on AI input.** An `Observation` / `TriageContext` carrying a known secret (in an
  element `value` / `label` and in text) is run through the input builder with an injected client.
  The test asserts that the captured payload carries masked values.
- **Fail closed.** With the provider's credential absent, each AI entry point exits with the clear
  error, and the injected client is never called.

## Alternatives considered

* **Offer a hosted convenience key / managed AI (the Maestro direction).** Rejected. It recreates
  the exact vendor-cloud and data-egress concern that differentiates Bajutsu. It also blurs the
  bring-your-own-key promise. Convenience here would cost the very thing being sold.
* **Leave it implicit (status quo).** Rejected as insufficient. The guarantee is not legible to a
  prospective adopter. The redaction-on-AI-input part is genuinely not guaranteed today. "Your
  data stays yours" is not yet something the tool can promise in writing.
* **A full local/offline model option bundled with the tool.** Out of scope here. The provider
  abstraction makes pointing at a self-hosted endpoint possible. Shipping and supporting a
  bundled model is a separate, much larger commitment.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- `bajutsu/anthropic_client.py`, the one provider factory
  (`provider` / `make_client` / `resolve_model` / `credential_gap`)
- `bajutsu/agent.py`, `bajutsu/claude_agent.py`, `bajutsu/alerts.py`, `bajutsu/redaction.py`
- `bajutsu/evidence.py` (where the run-scoped `Redactor` is built today)
- [recording.md](../../docs/recording.md), [DESIGN §2 / §3.1](../../DESIGN.md)
- [BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md), which extends the same
  redacted-path philosophy to the hosted serve's operational logs
