**English** · [日本語](BE-0130-default-network-secret-redaction-ja.md)

# BE-0130 — Redact sensitive network headers and cookies by default

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0130](BE-0130-default-network-secret-redaction.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0130") |
| Implementing PR | [#626](https://github.com/bajutsu-e2e/bajutsu/pull/626) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

Network-evidence redaction (`redact:`) exists but is entirely opt-in, and header-name matching is
exact rather than case-insensitive. This item makes a standard set of sensitive headers redacted
by default, and fixes header matching so `cookie` also masks `set-cookie`.

## Motivation

`Redact` (`bajutsu/scenario/models/evidence.py:47`) defaults every list — `labels`, `headers`,
`fields` — to empty, so a scenario that never mentions `redact:` gets a `Redactor` whose `active`
property is `False`. `_write_network` (`bajutsu/runner/pipeline.py:49`) still calls
`redactor.redact_exchange` unconditionally, but with an inactive redactor that call is a no-op
(`bajutsu/redaction.py:88`, the `if not self.active: return exchange` guard): every captured
exchange — headers, cookies, and bodies verbatim — is written as-is to
`runs/<id>/<sid>/network.json`. `Authorization` tokens, session `Cookie`/`Set-Cookie` values, and
any secret embedded in a request/response body persist on disk in every run's evidence unless the
author remembered to opt in.

This is a silent default-open posture in a tool whose evidence is routinely shared (a run's report
is designed to be inspected, attached to a bug report, or sent through `record`/`enrich`/`triage`
to the configured AI provider — see BE-0047, AI data sovereignty). A reviewer reading `network.json`
after a failed run should not be handed a live bearer token by default.

Independently, header matching is exact-name only: `_header_names` in `Redactor.__init__`
(`bajutsu/redaction.py:104`, the `{h.lower() for h in redact.headers}` set built from the
author-supplied names) matches only the literal header name given. An author who writes
`headers: [cookie]` — the natural, singular way to say "mask the session cookie" — does not get
`Set-Cookie` masked in the response, since `"set-cookie" != "cookie"`. The two names refer to the
same secret (a cookie sent by the client vs. issued by the server) and should be treated as one
concern.

## Detailed design

1. **Redact a standard sensitive-header set by default.** Introduce a built-in default list —
   `authorization`, `cookie`, `set-cookie`, `x-api-key`, and other common credential-bearing
   headers (`proxy-authorization`, `x-auth-token`) — masked even when a scenario's `redact:` is
   unset or omits `headers`. `Redactor` merges the scenario-supplied `headers` with this default
   set rather than requiring the author to opt in to baseline protection; `redact:` still lets an
   author add scenario-specific header/field names and element `labels` on top.
2. **Allow an explicit escape hatch, not a silent one.** An author who genuinely needs the raw
   value of a defaulted header (e.g. debugging an auth failure) can request it by naming it under
   a scenario option that disables a specific default (not by mere absence of `redact:`), so
   turning off protection is a visible, deliberate choice rather than the current implicit default.
3. **Normalize header-name matching.** Make `_header_names` comparisons case-insensitive
   end-to-end (already the intent, but verify request/response header dict keys are lowercased
   consistently before the set lookup) and treat `cookie` and `set-cookie` as linked: requesting
   either masks both, since they carry the same class of secret in opposite directions.
4. **Tests.** A scenario with no `redact:` block still masks `Authorization`/`Cookie`/`Set-Cookie`
   in `network.json`; a scenario requesting `headers: [cookie]` also masks `Set-Cookie`; the
   escape-hatch option surfaces the raw value only when explicitly set.
5. **Docs.** Document the default-masked header set and the escape hatch in the evidence/redaction
   docs (`docs/` and `docs/ja/`), and note that `cookie`/`set-cookie` are treated as one concern.

The encode-aware matching gap (percent-encoding, Basic-auth base64, HTML/JSON escaping) is a
separate, sibling item (encode-aware secret redaction) — this item is scoped to which headers are
masked by default and how header names are matched, not how values are matched. Nothing here
touches the deterministic `run`/CI pass/fail logic or introduces an LLM; redaction runs entirely in
the evidence-writing path.

## Alternatives considered

- **Leave redaction fully opt-in and just document the risk.** Rejected: the finding is that this
  is the current behavior and it already produces plaintext secrets in evidence by default; a
  tool whose evidence is shared and sent to an AI provider needs a safe default, not a stronger
  warning.
- **Require `redact: {}` (empty but present) to opt in to the default set, changing nothing for a
  fully-absent `redact:`.** Rejected as an unnecessary and confusing distinction between "no
  `redact:` key" and "`redact:` present but empty" — both should get the same safe baseline.
- **Mask every header unless explicitly allowlisted.** Rejected as too aggressive a default: most
  headers (`Content-Type`, `User-Agent`, custom app headers) are not secrets and masking them by
  default would make `network.json` far less useful as evidence without a corresponding security
  benefit.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Built-in default sensitive-header set masked unconditionally
- [x] Explicit, visible escape hatch to disable a specific default (no silent opt-out)
- [x] Header-name matching normalized (case-insensitive; `cookie` ↔ `set-cookie` linked)
- [x] Tests: default masking with no `redact:`, `cookie` also masks `Set-Cookie`, escape hatch
      opt-out
- [x] Docs updated (both languages)

- 2026-07-04: [#626](https://github.com/bajutsu-e2e/bajutsu/pull/626) — added the built-in
  default sensitive-header set, the `unmaskHeaders` escape hatch, and `cookie`/`set-cookie`
  linkage in `Redactor`; masked default headers unconditionally in `redact_exchange`; updated
  the redaction docs (both languages).

## References

- `bajutsu/scenario/models/evidence.py:47` — `Redact`, defaulting every list to empty.
- `bajutsu/runner/pipeline.py:49` — `_write_network`, writing `<sid>/network.json`.
- `bajutsu/redaction.py:88` — the `active` no-op guard in `redact_exchange`.
- `bajutsu/redaction.py:104` — `_header_names`, the exact-match header-name set.
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)
- [BE-0032 — Secret variables](../BE-0032-secret-variables/BE-0032-secret-variables.md)
- Originates from the 2026-07-02 codebase-analysis report (security).
