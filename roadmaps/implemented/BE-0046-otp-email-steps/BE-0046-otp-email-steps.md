**English** · [日本語](BE-0046-otp-email-steps-ja.md)

# BE-0046 — OTP & email side-channel steps

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0046](BE-0046-otp-email-steps.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | [#260](https://github.com/bajutsu-e2e/bajutsu/pull/260) (totp), [#308](https://github.com/bajutsu-e2e/bajutsu/pull/308) (email design), [#311](https://github.com/bajutsu-e2e/bajutsu/pull/311) (email) |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

The side-channel utility steps carved out of [BE-0036](../../implemented/BE-0036-utility-steps/BE-0036-utility-steps.md)
once its `http` slice shipped: `totp` (generate a time-based one-time password locally) and
`email` (poll a test mailbox and extract a received code). These bring an out-of-band
authentication value into the run so a real login flow can be automated.

## Motivation

A real-app login flow is rarely just typing a password. It commonly requires a side channel the
UI cannot supply by itself: a one-time password (OTP) from an authenticator, or a two-factor
(2FA) code delivered by email or SMS. `http` (BE-0036) already covers values fetched from an HTTP
endpoint, but the common 2FA cases come from an authenticator app or a real inbox. Without a way
to bring those values in, the most important flows — sign-in, account verification, password
reset — can't be automated deterministically. Authors are forced either to disable 2FA in test
builds (which then no longer exercises the real path) or to paste a value by hand (which isn't
reproducible).

**Competitive context (Maestro).** Maestro covers this same need by sending authors into its
JavaScript escape hatch — `runScript` / `evalScript` to compute a code or poll a mailbox — which
works but moves the logic out of the reviewable YAML and into general-purpose code whose effect on
the flow is opaque to the report (the same trade-off
[BE-0033](../../implemented/BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md)
avoided for control flow). Bajutsu's bet is a few purpose-built, auditable steps (`totp` /
`email`) that produce a value deterministically into `${vars.*}` and stay visible in the scenario
and the report. The differentiator is delivering the login-flow capability **without** a scripting
escape hatch and without an LLM, so the gate stays deterministic and the flow stays reviewable.

## Detailed design

Add two steps that produce a value into the existing `${vars.*}` namespace
([scenarios](../../../docs/scenarios.md#runtime-variables-vars)), so a later `type` / `assert` can
consume it deterministically:

```yaml
- totp: { secret: "${secrets.TOTP_SEED}", into: { var: code } }   # RFC 6238 time-based OTP
- type: { text: "${vars.code}", into: { id: auth.code } }
```

```yaml
- email: { match: { to: "test@example.com", subjectMatches: "code" }, extract: { var: code, bodyMatches: "[0-9]{6}" }, timeout: 30 }
- type: { text: "${vars.code}", into: { id: auth.otp } }
```

Mapping and contract:

- **`totp`** computes a time-based one-time password locally from a seed (RFC 6238). Pure
  computation, fully deterministic given the seed and the clock, no network.
- **`email`** polls a test mailbox API until a matching message arrives, then extracts the code.
  The wait is a **condition wait with a mandatory `timeout`** (the same rule as every other wait —
  no fixed sleep, no infinite poll); a timeout is a step failure.

Secrets (TOTP seeds, mailbox credentials, API base URLs) are referenced as `${secrets.X}` and
declared in config, so the scenario file stores tokens, never values, and they are auto-masked in
evidence. Endpoint configuration shared across scenarios lives in `apps.<name>`.

Prime directives preserved:

- **No LLM on the run path.** Each step is deterministic computation or a deterministic
  match — `totp` is arithmetic, `email` extracts by regex. The pass/fail judgment still comes only
  from machine-checkable assertions; the run/CI gate stays AI-free.
- **Determinism.** `email` waits on a condition with a bounded timeout, never a sleep. `totp` is
  immediate. A flaky external mailbox surfaces as a clean step failure, not a silent wrong value.
- **App-agnostic.** The steps and their wiring are identical across apps; only the endpoints and
  secrets differ.
- **Codegen.** These run in the bajutsu runner, not the app, and have no XCUITest equivalent, so
  codegen emits a labeled `// TODO` (per
  [BE-0026](../../in-progress/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)).

### Email step — the mailbox surface

The open design question the `totp` slice deferred was *what mailbox* `email` talks to. The decision
is a **generic HTTP polling mailbox**, not a provider-specific client: the step `GET`s a configured
endpoint that returns the inbox as JSON, filters for the awaited message, and extracts the code by
regex. This reuses the `http` step's HTTP plumbing (BE-0036), keeps the tool provider-neutral (any
mailbox with a readable HTTP API works — Mailosaur, MailSlurp, a team's own test SMTP-to-HTTP
bridge), and adds no provider SDK dependency.

**Config (`apps.<name>.mailbox`).** The endpoint and how to read its response live in config, so the
scenario stays app-agnostic and credential-free:

```yaml
apps:
  myapp:
    mailbox:
      url: "${secrets.MAILBOX_URL}"        # the inbox endpoint (GET); may carry a query/box id
      headers: { Authorization: "Bearer ${secrets.MAILBOX_TOKEN}" }
      # How to read this provider's JSON (dotted paths into the response), so no per-provider code:
      messages: "items"                     # path to the message array (default: the root array)
      fields: { to: "to", subject: "subject", body: "text", receivedAt: "receivedAt" }
```

Defaults match the common "array of messages with `to` / `subject` / `body` / `receivedAt`" shape, so
a conforming API needs no `messages` / `fields` mapping at all; the mapping exists only to absorb a
differently-named response without a code change.

**Step contract.**

```yaml
- email:
    match: { to: "test@example.com", subjectMatches: "verification" }   # which message to wait for
    extract: { var: code, bodyMatches: "[0-9]{6}" }                     # pull the value into vars.code
    timeout: 30
```

- **`match`** selects the message: `to` (exact) and/or `subject` (exact) / `subjectMatches` (regex),
  combined with AND. At least one criterion is required (a match-anything `email` is rejected at load
  time, like an empty selector).
- **`extract`** pulls the value from the matched message's body into `${vars.<var>}`: `bodyMatches`
  is a regex whose **first capturing group** (or whole match, if none) is the value. The value lives
  only in runtime `vars.*`; the recorded scenario keeps the `${vars.*}` token (like `totp` / `http`
  `saveBody`), so it never enters the manifest/report.
- **`timeout`** (mandatory, seconds) bounds the poll — the same condition-wait rule as every other
  wait, no fixed sleep, no infinite poll.

**Determinism — the parts that matter.**

- **Only mail that arrives *after* the step starts counts.** The step records the set of **message
  ids** present when it began, and ignores those ids — so a stale code left in the mailbox by an
  earlier run is never matched. Keying on id (rather than comparing the provider's `receivedAt` to
  the local run clock) is **skew-free**: it needs no agreement between the mailbox's clock and the
  runner's. A message with no provider id gets a stable content-hash id, so the baseline still
  distinguishes it.
- **A unique awaited message.** With the after-start boundary the expected case is exactly one new
  matching message. If more than one matches, the **newest by `receivedAt`** wins (stable tie-break
  by message id), so the result never depends on arrival-order races. `receivedAt` is used only for
  this ordering, never as the after-start gate.
- **Bounded poll.** Re-fetch and re-check until a match or the `timeout` deadline — the standard
  condition-wait pattern (repeat fetch + test), not a fixed sleep. A timeout, a matched message
  whose body the `extract` regex does not hit, or a non-2xx fetch is a **clean step failure**, never
  a silent wrong value.

**Gate-testability.** The fetch is taken behind an injectable HTTP function (the `http` step's
client / a `RunFn`-style seam), so the poll loop, the `after`-start filter, the match/extract, the
newest-wins tie-break, and the timeout are all unit-tested over fabricated mailbox responses with no
network. The live HTTP call is the only mocked surface — the permitted "external API call" exception
— exactly as `http` is tested.

### Implementation status

The **`totp`** slice ships — the half with no external dependency. `totp: { secret, into: { var } }`
computes an RFC 6238 code from the base32 `secret` (commonly `${secrets.*}`, interpolated before the
step runs) and the current time, writing it to `${vars.<var>}` for a later `type` / `assert`. The
algorithm is a pure, gate-tested function (`bajutsu/totp.py`, checked against the RFC 6238 vectors);
the step is a thin handler that calls it at wall-clock time (`bajutsu/orchestrator/actions/handlers/totp.py`).
It follows the `http`/`saveBody` precedent for producing a value into `vars.*`, touches no device,
and emits a labeled `// TODO` from codegen.

The **`email`** slice ships, completing the item. `email: { match, extract, timeout }`
(`bajutsu/scenario/models/actions.py`) polls the configured `apps.<name>.mailbox`
(`bajutsu/config.py`) until a message arriving *after* the step started satisfies `match`, then
extracts the value by `bodyMatches` into `${vars.<var>}`. The match / extraction / response-shape
reading / after-start selection are pure, gate-tested functions (`bajutsu/mailbox.py`); the bounded
poll runs in the run loop (`_do_email`, `orchestrator/loop.py`) over an **injected** `MailboxReader`
(`orchestrator/types.py`), built once per run from config with `${secrets.*}` resolved
(`runner/mailbox.py`) — so only the live HTTP GET touches the network and the logic is tested with no
network. The after-start boundary keys on **message id** (the set present at the step start), which
is skew-free — it never compares the provider's timestamp to the local clock; `receivedAt` only
orders newest-first among new matches. A timeout, a matched message whose body the regex can't hit,
or an unreachable / non-2xx mailbox is a clean step failure. The produced value lives only in runtime
`vars.*`; the recorded scenario keeps the `${vars.*}` token (like `totp` / `http`), so it never
enters the manifest/report. Codegen emits a labeled `// TODO`. No device, no LLM.

## Alternatives considered

- **Disable 2FA / OTP in test builds and skip the side channel entirely.** Simplest, and fine for
  a smoke test, but it stops exercising the real authentication path — exactly the flow most worth
  covering. Rejected as the only option; a build may still offer a test bypass, but the tool
  should not require it.
- **Mock the OTP/email provider with `mocks`.** `mocks` stubs the app's *own* outgoing traffic, so
  it works when the code arrives over a request the app makes and observes. It does not help when
  the value comes from an out-of-band channel (an authenticator app, a real inbox), which is the
  common 2FA case. Kept as complementary, not a replacement.
- **Reuse `http` (BE-0036) for everything.** `http` fetches a value from an endpoint, which covers
  a backend-issued token but not a locally computed TOTP nor a polled inbox. Kept for the HTTP
  case; `totp` / `email` cover the rest. (`email` *reuses* `http`'s HTTP plumbing internally, but
  adds the polling, the match/extract, and the after-start determinism boundary that a one-shot
  `http` fetch cannot express.)
- **A provider-specific mailbox client (Mailosaur / MailSlurp SDK).** Quickest to wire for one
  provider, but it binds the tool to that vendor's SDK and API, and a second provider means a second
  client. Rejected in favour of the generic HTTP surface with configurable field paths, which reads
  any provider's JSON inbox with no added dependency; a provider that only offers a non-HTTP API can
  still be fronted by a tiny bridge.
- **Poll a raw IMAP mailbox.** Works against a real inbox without a test-mail provider, but pulls in
  an IMAP client and credential handling, and parsing MIME for the body is its own surface. Deferred:
  the HTTP mailbox covers the test-provider case the motivating 2FA flows use; an IMAP source can be
  added later behind the same `email` step contract if demand appears.

## References

Split out of [BE-0036 — HTTP utility step](../../implemented/BE-0036-utility-steps/BE-0036-utility-steps.md).
[scenarios.md](../../../docs/scenarios.md)
