**English** · [日本語](BE-0047-ai-data-sovereignty-ja.md)

# BE-0047 — AI data sovereignty (provider-agnostic, redacted AI path)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0047](BE-0047-ai-data-sovereignty.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
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
  `defaults` / `apps.<name>`) so the endpoint, model, and key source are explicit and swappable —
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

## References

`bajutsu/agent.py`, `bajutsu/claude_agent.py`, `bajutsu/alerts.py`, `bajutsu/redaction.py`,
[recording.md](../../../docs/recording.md), [DESIGN §2 / §3.1](../../../DESIGN.md),
[BE-0055](../../implemented/BE-0055-operational-logging/BE-0055-operational-logging.md) — which extends
the same redacted-path philosophy to the hosted serve's operational logs.
