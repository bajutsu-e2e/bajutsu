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
| Topic | Candidates from competitive research (Maestro) |
| Origin | Maestro |
<!-- /BE-METADATA -->

## Introduction

Make it an explicit, enforced guarantee that Bajutsu's AI paths (`record`, `triage`,
`--dismiss-alerts`) run under the user's own API key and provider, that everything sent to the
model passes through the existing redaction layer first, and that the provider behind the
authoring / investigation agent is pluggable. This is a trust and data-handling guarantee, not a
runtime check — the gate stays AI-free regardless.

## Motivation

A concrete 2026 competitive shift sharpened this. Maestro moved its AI features to a
**Cloud-managed** model: bring-your-own-key was removed (`MAESTRO_CLI_AI_KEY` /
`MAESTRO_CLI_AI_MODEL` deprecated), AI commands now route through Maestro Cloud over opaque
"third-party AI providers", and using them requires a Maestro account — screenshots of the app
under test are sent to a vendor service the user does not control. For regulated industries and
privacy-sensitive teams, that is a hard adoption blocker.

Bajutsu is already the opposite: its AI paths use the user's own `ANTHROPIC_API_KEY`, run locally,
and the deterministic gate calls no model at all. But today that is an **emergent property**, not
a documented and enforced promise. A buyer cannot point to a guarantee, and one part is genuinely
unenforced: redaction is applied to written *evidence*, but there is no guarantee that the
screenshots and element trees sent to the model during `record` / `triage` / `--dismiss-alerts`
are redacted first. This item turns "your AI, your key, your data" from an accident into a
first-class, verifiable property — a direct contrast with vendor-cloud AI.

## Detailed design

Proposal altitude. Three guarantees, each building on a seam that already exists.

- **Provider is pluggable.** The `Agent` / `claude_agent` abstraction (`bajutsu/agent.py`,
  `bajutsu/claude_agent.py`) already isolates the model call. Formalize a provider config (under
  `defaults` / `targets.<name>`) so the endpoint, model, and key source are explicit and swappable —
  e.g. point the authoring / triage agent at a self-hosted gateway or an enterprise proxy. Claude
  (Anthropic) stays the default; nothing here weakens that.
- **Redaction on AI inputs.** Route the screenshots, element trees, and logs handed to `record` /
  `triage` / `--dismiss-alerts` through `bajutsu/redaction.py` *before they leave the process*,
  and document the guarantee. This closes the gap between "evidence is redacted" and "what the
  model sees is redacted".
- **No key, no AI (fail closed).** The AI paths require an explicitly configured key and never
  silently fall back to a hosted default. Absence of a key is a clear error, not a quiet
  round-trip to someone else's cloud.

This stays strictly on the Tier-1 side. It changes nothing about `run`: pass/fail remains
machine-only, no model is added to the gate. It is a trust / positioning feature with concrete
enforcement, not a new kind of check.

The implementation-level design of each guarantee follows.

### Provider configuration

Today the provider is **env-only** (`bajutsu/anthropic_client.py`: `BAJUTSU_AI_PROVIDER`,
`ANTHROPIC_API_KEY`, `BAJUTSU_BEDROCK_MODEL`, the AWS chain). Formalize it as an optional `ai` block
resolved like any other setting — `defaults.ai`, overridable per `targets.<name>.ai`:

```yaml
defaults:
  ai:
    provider: anthropic                      # anthropic (default) | bedrock
    model: claude-opus-4-8                    # optional: override the path's default model
    baseUrl: https://ai-gateway.internal/v1  # optional: self-hosted gateway / enterprise proxy (anthropic)
    keyEnv: ANTHROPIC_API_KEY                 # the NAME of the env var holding the key — never the key
```

- **Keys never live in config.** `keyEnv` names an environment variable; the value is read from the
  environment at call time, so a secret never lands in the repo or in an uploaded bundle (CLAUDE.md).
  `baseUrl` points the Anthropic SDK at a self-hosted gateway / proxy
  (`Anthropic(base_url=…, api_key=os.environ[keyEnv])`); Bedrock keeps the AWS credential chain.
- **Config first, env fallback.** `provider()` / `make_client()` / `resolve_model()` — the single
  factory every AI path already shares — read the resolved `ai` config first and fall back to today's
  env vars, so existing setups keep working unchanged. The block resolves into `Effective`, so the
  CLI and `serve` agree on one source of truth.
- **App-agnostic.** It is config, not code: the agent / locator call sites are untouched.

### Redaction on AI inputs — and the screenshot caveat

The run-scoped `Redactor` (`bajutsu/redaction.py`, built from the target's `redact` keys + resolved
secret values, exactly as evidence writing already builds it in `bajutsu/evidence.py`) is applied to
the **textual** model inputs before they leave the process:

- **record** — `claude_agent._render` / `_user_content`: the screen's element list (`label` / `value`
  / `traits`) is `redact_elements`-ed before it is rendered into the user message.
- **triage** — `TriageContext.elements` and the failure text: redacted before the prompt is assembled.
- **free text / logs** handed to any AI path: `redact_text`. This includes `--dismiss-alerts`,
  which sends a screenshot **plus** a small text block (the image dimensions and an optional,
  possibly user-supplied `--alert-instruction`); the instruction is run through `redact_text` too.

**The honest limit: screenshots are images, and `redaction.py` masks text and element trees, not
pixels.** record / triage / dismiss-alerts each send a screenshot, which redaction cannot scrub. The
guarantee is therefore two-part: (1) **textual** inputs (element trees, logs) are redacted before
send; (2) **every** input — screenshots included — goes only to the **user-configured
provider/endpoint**, never a vendor default, so a screenshot never crosses the user's trust boundary.
For teams that need the pixels off the wire too, an optional `ai.sendScreenshots: false` (text-only,
at an accuracy cost) is offered; pixel-level image redaction is a separate, much larger effort and
stays out of scope.

### Fail closed

The AI entry points (`record`, `triage`, `--dismiss-alerts`) hard-fail with a clear,
provider-specific error when the selected provider has no usable credential
(`anthropic_client.credential_gap()` is non-None) — they never construct a client that falls back to
a hosted default. This tightens the existing `crawl` / `run` gate-or-warn into a strict fail-closed on
the authoring / investigation paths: a missing key is an actionable error, not a quiet round-trip to
someone else's cloud.

### Validation

All machine-checkable in the fast gate — no real API, since the SDK client is already injectable
(`make_client(client=…)`), and no test puts an LLM on the `run` / CI gate:

- **Provider resolution.** A recording fake client asserts the endpoint / model / key-env resolve
  from the `ai` config, with config-over-env precedence and the env fallback intact.
- **Redaction on AI input.** An `Observation` / `TriageContext` carrying a known secret (in an
  element `value` / `label` and in text) is run through the input builder with an injected client;
  the captured payload is asserted masked.
- **Fail closed.** With the provider's credential absent, each AI entry point exits with the clear
  error and the injected client is never called.

## Alternatives considered

* **Offer a hosted convenience key / managed AI (the Maestro direction).** Rejected: it recreates
  the exact vendor-cloud and data-egress concern that differentiates Bajutsu, and it blurs the
  bring-your-own-key promise. Convenience here would cost the very thing being sold.
* **Leave it implicit (status quo).** Rejected as insufficient: the guarantee is not legible to a
  prospective adopter, and the redaction-on-AI-input part is genuinely not guaranteed today, so
  "your data stays yours" is not yet something the tool can promise in writing.
* **A full local/offline model option bundled with the tool.** Out of scope here — the provider
  abstraction makes pointing at a self-hosted endpoint possible, but shipping and supporting a
  bundled model is a separate, much larger commitment.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/anthropic_client.py` (the one provider factory — `provider` / `make_client` /
`resolve_model` / `credential_gap`), `bajutsu/agent.py`, `bajutsu/claude_agent.py`,
`bajutsu/alerts.py`, `bajutsu/redaction.py`, `bajutsu/evidence.py` (where the run-scoped `Redactor`
is built today), [recording.md](../../docs/recording.md), [DESIGN §2 / §3.1](../../DESIGN.md),
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) — which extends
the same redacted-path philosophy to the hosted serve's operational logs.
