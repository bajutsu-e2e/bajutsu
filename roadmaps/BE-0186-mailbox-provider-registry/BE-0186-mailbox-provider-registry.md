**English** · [日本語](BE-0186-mailbox-provider-registry-ja.md)

# BE-0186 — Mailbox provider registry for the email step

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0186](BE-0186-mailbox-provider-registry.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0186") |
| Implementing PR | [#953](https://github.com/bajutsu-e2e/bajutsu/pull/953) |
| Topic | Candidates from competitive research (MagicPod / Autify) |
<!-- /BE-METADATA -->

## Introduction

Apply Bajutsu's **backend-agnostic** philosophy — *a platform is a backend behind one interface* —
to where the `email` step (BE-0046) reads its mail: **a mailbox is a backend behind one interface.**
The step already talks to its inbox through a single `MailboxReader` seam that returns normalized
messages (`MailboxMessage`); today that seam has exactly one implementation, a generic HTTP-JSON
reader. This item turns that single hardcoded path into a **provider registry keyed by transport
protocol** (`http`, later `imap`), selected per target in config, so adding a second kind of mailbox
is *registering an adapter*, not *editing the runner* — the same move BE-0104 made for AI providers.

The registry ships with its HTTP reference adapter (the existing reader, re-homed behind the
registry) and nothing else; IMAP and any further transports are deliberately deferred to follow-up
items that plug into the seam this one defines.

## Motivation

BE-0046 chose a generic HTTP-JSON mailbox precisely to stay provider-neutral without a vendor SDK,
and its `messages` / `fields` mapping already absorbs the naming differences *between JSON
providers*. What it does not cover is a mailbox that speaks a **different transport** — most
notably IMAP, the one standard protocol real mail services share, which BE-0046 explicitly deferred
"behind the same `email` step contract if demand appears". Reaching an IMAP inbox today would mean
editing `bajutsu/runner/mailbox.py` to branch on the source, which is exactly the factory
`if`-chain BE-0104 replaced with a registry for AI providers.

Two forces make the registry the right shape now:

- **The seam already exists.** `MailboxReader` (a `fetch(timeout) -> list[MailboxMessage]`
  protocol) and the neutral `MailboxMessage` type are the mailbox equivalents of BE-0104's
  `AiBackend` protocol and its neutral message types. Two of the three registry layers are already
  built; only the name → adapter table is missing.
- **The deterministic core must not grow a transport `if`-chain.** The poll loop, the after-start
  baseline, the match/extract, and the timeout live in the pure `bajutsu/mailbox.py` and the run
  loop, and must stay unchanged as sources multiply. A registry keeps every transport-specific
  detail on the periphery, behind the one interface the core already depends on.

This is a Tier-1/config concern only: no prime directive is touched. The pass/fail judgement still
comes from the deterministic runner; the `email` step's determinism guarantees (skew-free
after-start selection, bounded poll, regex extract) are properties of the core, which this item
does not modify.

## Detailed design

The change is entirely on the periphery — the scenario-facing `email` contract and the
deterministic core (`bajutsu/mailbox.py`, the `_do_email` poll loop) are unchanged.

### The registry (mirrors `bajutsu/ai/registry.py`)

- **Key on transport protocol, never on vendor.** The registry maps a `kind` (`http`, later
  `imap`) to an adapter that builds a `MailboxReader` from the resolved mailbox config. It does
  **not** key on `mailosaur` / `mailslurp` / `gmail`: those differ only in JSON field names, which
  the existing `fields` mapping already absorbs one level down. Keying on vendor would reproduce
  the per-vendor client BE-0046 rejected.
- **`register(kind, adapter)` + fail-closed resolution.** An unknown `kind` fails closed the first
  time a run resolves the mailbox (a clean config error), exactly as BE-0104's `_provider_name`
  raises on an unregistered provider — never a silent fallback.
- **Built-in HTTP reference adapter.** The existing `build_mailbox_reader` HTTP-JSON reader is
  re-homed as the built-in `http` adapter; it remains the only shipped adapter.

### Config — selection lives in config, not the scenario (`mailbox.kind`)

The provider is chosen per target in config, keeping the scenario app-agnostic and credential-free
(the BE-0046 rule), and consistent with BE-0104 selecting the AI provider in config
(`defaults.ai` / `targets.<name>.ai`), never per call site:

```yaml
targets:
  myapp:
    mailbox:
      kind: http            # default when omitted → the existing reader, so old configs are unchanged
      url: "${secrets.MAILBOX_URL}"
      headers: { Authorization: "Bearer ${secrets.MAILBOX_TOKEN}" }
      messages: "items"
      fields: { body: "text" }
```

- **`kind` is optional and defaults to `http`.** Every existing `mailbox:` block keeps working
  byte-for-byte (back-compat, like BE-0104 keeping the legacy `anthropic` provider name resolving).
- **Credentials / endpoints stay in config**, referenced as `${secrets.*}`; nothing about the
  provider leaks into the scenario file.

### Optional: named mailboxes referenced from the step (`email.from`)

When a target legitimately has more than one inbox (e.g. one HTTP test mailbox and one IMAP box),
the scenario names *which logical inbox* to read — it references a config entry by name, it does
**not** embed a provider or credentials, so the scenario stays portable:

```yaml
targets:
  myapp:
    mailboxes:                     # named definitions (superset of the single `mailbox:`)
      primary: { kind: http, url: "${secrets.MAILBOX_URL}" }
      otp:     { kind: imap, host: "${secrets.IMAP_HOST}" }
```

```yaml
- email: { from: otp, match: { subjectMatches: "code" }, extract: { var: code, bodyMatches: "[0-9]{6}" }, timeout: 30 }
```

- **`from` is optional**, defaulting to the sole / `primary` mailbox, so the single-mailbox scenario
  is unchanged. `from` selects a *logical inbox name*, never a transport or a vendor.
- This layer is **carried in the design for completeness but may ship after the registry** — the
  registry alone already delivers "split the implementation, choose it in config". Named mailboxes
  are only needed once a target has more than one inbox.

### Scope boundary

- **In:** the registry + fail-closed resolution, the `http` reference adapter (re-homed existing
  reader), the `mailbox.kind` config field with an `http` default and back-compat, and unit tests
  over the registry / resolution / default.
- **Deferred to follow-ups:** the IMAP adapter (client, credential handling, MIME body parsing) and
  the named-mailbox `email.from` layer. Each plugs into the seam this item defines, with no core
  change.

### Prime directives preserved

- **No LLM on the run path.** The registry is a deterministic name → adapter table; resolving and
  fetching a mailbox involves no LLM. The verdict still comes only from machine-checkable
  assertions.
- **Determinism.** The `email` core (after-start baseline, bounded poll, regex extract) is
  untouched; a misconfigured or unknown `kind` is a clean step / config failure, never a silent
  wrong value.
- **App-agnostic.** Provider, endpoint, and credentials stay in `targets.<name>` config; the
  scenario keeps the same `email` contract, at most naming a logical inbox.

## Alternatives considered

- **Key the registry on the mail vendor (`mailosaur` / `mailslurp` / `gmail`).** Rejected: within a
  transport these differ only in JSON field names, which the existing `fields` mapping already
  absorbs, so vendor-keyed adapters would be near-identical readers multiplying per vendor — the
  per-provider client BE-0046 already rejected. Keying on transport (`http` / `imap`) keeps the
  adapter count proportional to protocols, not vendors.
- **Select the provider in the scenario YAML.** Rejected: it ties a scenario to a provider and
  risks credentials leaking into the scenario, breaking BE-0046's app-agnostic / credential-free
  rule and diverging from BE-0104, which selects the AI provider in config. The scenario may at
  most name a *logical inbox* (`email.from`), which references a config entry, not a provider.
- **Leave the single HTTP reader and add an `if` for IMAP when needed.** Rejected: that reintroduces
  the factory `if`-chain BE-0104 removed, and pushes a second transport's concerns into the runner.
  The registry costs one small layer now and makes every later transport a drop-in.
- **Ship the IMAP adapter in this item.** Deferred, not rejected: BE-0104's precedent is "seam plus
  one reference adapter, second backend follows" — proving the seam with the provider already in use
  keeps the change small and low-risk. IMAP (client + credentials + MIME parsing) is its own
  surface and lands behind the seam this item defines.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Registry + fail-closed resolution (name → adapter table, unknown `kind` raises)
- [x] `http` reference adapter — re-home the existing `build_mailbox_reader` reader behind the registry
- [x] `mailbox.kind` config field, defaulting to `http` with byte-for-byte back-compat
- [x] Unit tests over the registry, fail-closed resolution, and the `http` default
- [x] Docs: `docs/configuration.md` + `docs/ja/` note that the mailbox transport is configurable
- [ ] (Follow-up) IMAP adapter behind the same seam
- [ ] (Follow-up) named mailboxes + `email.from` selection

### Log

- Registry + `http` reference adapter, `mailbox.kind` config field, unit tests, and bilingual config
  docs — the full in-scope slice; IMAP and named mailboxes stay deferred. [#953](https://github.com/bajutsu-e2e/bajutsu/pull/953)

## References

- [BE-0046 — `totp` / `email` steps](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) — the
  origin: introduced the `email` step, the generic HTTP mailbox, and the deferred IMAP option this
  item builds the seam for.
- [BE-0104 — vendor-neutral AI backend](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
  — the precedent this mirrors: *a backend behind one interface*, registry keyed by name, config-level
  selection, seam plus one reference adapter.
- [`bajutsu/mailbox.py`](../../bajutsu/mailbox.py), [`bajutsu/runner/mailbox.py`](../../bajutsu/runner/mailbox.py),
  [`bajutsu/orchestrator/types.py`](../../bajutsu/orchestrator/types.py) — the pure core, the current
  HTTP reader, and the `MailboxReader` protocol this item registers against.
