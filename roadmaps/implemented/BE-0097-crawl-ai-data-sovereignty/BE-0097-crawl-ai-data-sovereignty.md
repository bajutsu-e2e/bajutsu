**English** · [日本語](BE-0097-crawl-ai-data-sovereignty-ja.md)

# BE-0097 — AI data sovereignty for the crawl guide and serve-spawned AI paths

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0097](BE-0097-crawl-ai-data-sovereignty.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Implementing PR | [#380](https://github.com/bajutsu-e2e/bajutsu/pull/380) |
| Topic | Candidates from competitive research (Maestro) |
<!-- /BE-METADATA -->

## Introduction

Extend the AI data-sovereignty guarantees that [BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
shipped for `record` / `triage` / `--dismiss-alerts` to the **`crawl --guide ai`** path (and its
alert guard), and pin down how the guarantee reaches AI runs launched by **`serve`**. After BE-0047
the authoring/investigation paths run under the user's configured provider, redact their textual
inputs, and fail closed without a key — but the crawl AI guide does none of this yet, so "your AI,
your key, your data" is still only partly true. This closes that gap; it stays Tier-1 and adds no
model to the `run`/CI gate.

## Motivation

BE-0047 made three guarantees concrete for the authoring/investigation paths: a config-driven,
pluggable provider (`defaults.ai` / `targets.<name>.ai` → `Effective.ai`), redaction of the textual
inputs sent to the model, and fail-closed behaviour when no credential is configured. Its own report
flagged the remainder: `crawl` and `serve` were left out of scope.

Today that shows up concretely:

- **`crawl --guide ai`** reaches Claude through the same `anthropic_client` factory, but constructs
  its agent and alert guard **without** the resolved `eff.ai` provider config and **without** the
  run-scoped `Redactor`. So a crawl's AI guide sends the screen's element tree (and the alert
  guard's instruction text) to the model unredacted, and ignores a self-hosted-gateway / enterprise
  proxy configured in `ai`. Its credential check is the env-only `credential_gap()`
  (`bajutsu/cli/commands/crawl.py` `_ai_credential_gap`), not the provider-aware one BE-0047 added.
- **`serve`** spawns `run` / `record` / `crawl` as subprocesses (`bajutsu/serve/jobs.py`
  `_spawn_env`), passing `ANTHROPIC_API_KEY` through the environment. The spawned `record` / `triage`
  already inherit BE-0047 (they read `defaults.ai` from the bound config and redact), but `crawl`
  does not yet, and the serve UI's key-setting affordance (`set_api_key`) writes only
  `ANTHROPIC_API_KEY` — it does not honour a config `keyEnv`.

For a privacy-sensitive team evaluating Bajutsu against vendor-cloud AI, an un-redacted crawl guide
is exactly the kind of silent data egress BE-0047 exists to rule out. The guarantee should hold for
*every* AI path, not most of them.

## Detailed design

Reuse BE-0047's seams; no new mechanism. The work is threading the same three guarantees through the
crawl path and confirming the serve path.

- **Provider config + redaction for the crawl guide.** Thread the resolved `eff.ai` into the agent
  the crawl guide builds (the `make_agent` / `ClaudeAgent` construction in the crawl path) and into
  its alert guard (`ClaudeAlertLocator`), exactly as BE-0047 did for `record`. Pass the run-scoped
  `Redactor` (built from the target's `redact` keys + secret values, as evidence already builds it)
  so the element tree and the alert instruction are masked before they leave the process. Screenshots
  remain images — the same documented limit as BE-0047: text is redacted, and *all* inputs go only
  to the user-configured provider, never a vendor default.
- **Fail closed for the crawl guide.** Replace crawl's env-only `_ai_credential_gap` with the
  provider-aware `credential_gap(eff.ai)`, so `crawl --guide ai` (and the alert guard) hard-fail with
  a clear, provider-specific error when no credential is configured — never a quiet round-trip to a
  hosted default, matching `record` / `triage`.
- **Serve inheritance, made explicit.** A serve-spawned `crawl` reads `defaults.ai` /
  `targets.<name>.ai` from the bound config like the local CLI, so it picks up the provider config
  and redaction for free once the crawl path above honours them. Close the one serve-specific gap:
  the UI key affordance and `_spawn_env` should set the env var named by the active config's
  `keyEnv` (defaulting to `ANTHROPIC_API_KEY`), so a non-default `keyEnv` works under serve too.

The deterministic `run` / CI gate is untouched: this is all Tier-1 (crawl exploration's guide and
serve's authoring jobs), pass/fail stays machine-only, and no model is added to the gate. The knobs
are the existing `ai` config (app-agnostic).

### Validation

All fast-gate, no live API (the SDK client is injectable, as BE-0047's tests rely on):

- The crawl guide's agent + alert guard receive `eff.ai` and a `Redactor`; a known secret in a
  crawled element's value/label and in the alert instruction is masked in the captured payload.
- `crawl --guide ai` exits with the clear error when the configured provider has no credential, and
  the client is never constructed.
- A serve-spawned crawl resolves the provider config from the bound config; a non-default `keyEnv`
  is exported into the spawned env.

## Alternatives considered

- **Fold this into BE-0047 instead of a new item.** Rejected: BE-0047 is shipped (`Implemented`);
  extending a closed item is exactly what a new, traceable BE is for. This item references BE-0047
  as its basis.
- **Leave crawl out — it's a Tier-1 explorer, not a gate.** Rejected: the data-egress concern is
  about *what leaves the process to a model*, which the crawl guide does on every step; the
  sovereignty guarantee is meaningless if one AI path is exempt.
- **Block `crawl --guide ai` unless redaction is configured.** Rejected as too blunt: redaction is
  best-effort masking, not a precondition; the right shape is "always redact what we can, always send
  only to your provider", identical to BE-0047, not a new gate.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [BE-0047 — AI data sovereignty](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
  — the shipped guarantee this extends; reuse its `ai` config, `Redactor` threading, and fail-closed pattern.
- `bajutsu/anthropic_client.py` (`AiConfig` / `make_client` / `resolve_model` / `credential_gap`),
  `bajutsu/cli/commands/crawl.py` (`_ai_credential_gap`, the guide agent + alert guard construction),
  `bajutsu/crawl.py`, `bajutsu/alerts.py`, `bajutsu/redaction.py`, `bajutsu/serve/jobs.py`
  (`_spawn_env`), `bajutsu/serve/operations.py` (`set_api_key`) — the surfaces a fix touches.
- [DESIGN §2 / §3.1](../../../DESIGN.md) — Tier-1 AI, deterministic gate.
