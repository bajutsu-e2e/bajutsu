**English** · [日本語](BE-0046-otp-email-steps-ja.md)

# BE-0046 — OTP & email side-channel steps

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0046](BE-0046-otp-email-steps.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0046") |
| Implementing PR | [#260](https://github.com/bajutsu-e2e/bajutsu/pull/260) (totp), [#308](https://github.com/bajutsu-e2e/bajutsu/pull/308) (email design), [#311](https://github.com/bajutsu-e2e/bajutsu/pull/311) (email) |
| Topic | Scenario authoring features |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

This item adds two side-channel utility steps, carved out of
[BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md) once its `http` slice shipped. `totp`
generates a time-based one-time password locally. `email` polls a test mailbox and extracts a
received code. Both bring an out-of-band authentication value into the run, so a real login flow can
be automated.

## Motivation

A real-app login flow is rarely just typing a password. It commonly requires a side channel that
the UI cannot supply by itself. Examples are a one-time password (OTP) from an authenticator and a
two-factor authentication (2FA) code delivered by email or SMS.

`http` (BE-0036) already covers values fetched from an HTTP endpoint. The common 2FA cases, however,
come from an authenticator app or a real inbox. Without a way to bring those values in, the most
important flows (sign-in, account verification, and password reset) cannot be automated
deterministically. Authors must either disable 2FA in test builds, which no longer exercises the
real path, or paste a value by hand, which is not reproducible.

**Competitive context (Maestro).** Maestro covers this same need through its JavaScript escape
hatch: `runScript` / `evalScript` compute a code or poll a mailbox. That works, but it moves the
logic out of the reviewable YAML and into general-purpose code. The effect of that code on the flow
is opaque to the report. [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md)
avoided the same trade-off for control flow.

Bajutsu's bet is a few purpose-built, auditable steps (`totp` / `email`). Each produces a value
deterministically into `${vars.*}` and stays visible in the scenario and the report. The
differentiator is delivering the login-flow capability **without** a scripting escape hatch and
without an LLM, so the gate stays deterministic and the flow stays reviewable.

## Detailed design

Two new steps produce a value into the existing `${vars.*}` namespace
([scenarios](../../docs/scenarios.md#runtime-variables-vars)). A later `type` / `assert` step then
consumes the value deterministically:

```yaml
- totp: { secret: "${secrets.TOTP_SEED}", into: { var: code } }   # RFC 6238 time-based OTP
- type: { text: "${vars.code}", into: { id: auth.code } }
```

```yaml
- email: { match: { to: "test@example.com", subjectMatches: "code" }, extract: { var: code, bodyMatches: "[0-9]{6}" }, timeout: 30 }
- type: { text: "${vars.code}", into: { id: auth.otp } }
```

Mapping and contract:

- **`totp`** computes a time-based one-time password locally from a seed (RFC 6238). It is pure
  computation: fully deterministic given the seed and the clock, with no network.
- **`email`** polls a test mailbox API until a matching message arrives, then extracts the code.
  The wait is a **condition wait with a mandatory `timeout`**, the same rule as every other wait:
  no fixed sleep and no infinite poll. A timeout is a step failure.

Secrets (TOTP seeds, mailbox credentials, and API base URLs) are referenced as `${secrets.X}` and
declared in config. The scenario file therefore stores tokens, never values, and evidence masks the
values automatically. Endpoint configuration shared across scenarios lives in `targets.<name>`.

Prime directives preserved:

- **No LLM on the run path.** Each step is deterministic computation or a deterministic match:
  `totp` is arithmetic, and `email` extracts by regex. The pass/fail judgment still comes purely
  from machine-checkable assertions, and the run/CI gate stays AI-free.
- **Determinism.** `email` waits on a condition with a bounded timeout, never a sleep. `totp` is
  immediate. A flaky external mailbox surfaces as a clean step failure, not a silent wrong value.
- **App-agnostic.** The steps and their wiring are identical across apps. Only the endpoints and
  secrets differ.
- **Codegen.** These steps run in the bajutsu runner, not the app, and have no XCUITest
  equivalent. Codegen therefore emits a labeled `// TODO`, per
  [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md).

### The mailbox surface of the email step

The `totp` slice deferred one open design question: *what mailbox* `email` talks to. The decision
is a **generic HTTP polling mailbox**, not a provider-specific client. The step `GET`s a configured
endpoint that returns the inbox as JSON, filters for the awaited message, and extracts the code by
regex.

This design reuses the `http` step's HTTP plumbing (BE-0036). It keeps the tool provider-neutral:
any mailbox with a readable HTTP API works, such as Mailosaur, MailSlurp, or a team's own test
SMTP-to-HTTP bridge. It also adds no provider SDK dependency.

**Config (`targets.<name>.mailbox`).** The endpoint and the way to read its response live in
config, so the scenario stays app-agnostic and credential-free:

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

Defaults match the common "array of messages with `to` / `subject` / `body` / `receivedAt`" shape.
A conforming API therefore needs no `messages` / `fields` mapping at all. The mapping exists merely
to absorb a differently-named response without a code change.

**Step contract.**

```yaml
- email:
    match: { to: "test@example.com", subjectMatches: "verification" }   # which message to wait for
    extract: { var: code, bodyMatches: "[0-9]{6}" }                     # pull the value into vars.code
    timeout: 30
```

- **`match`** selects the message by `to` (exact) and/or `subject` (exact) / `subjectMatches`
  (regex), combined with AND. At least one criterion is required. A match-anything `email` is
  rejected at load time, like an empty selector.
- **`extract`** pulls the value from the matched message's body into `${vars.<var>}`. `bodyMatches`
  is a regex whose **first capturing group** (or whole match, if none) is the value. The value lives
  merely in runtime `vars.*`. The recorded scenario keeps the `${vars.*}` token, like `totp` / `http`
  `saveBody`, so the value never enters the manifest or the report.
- **`timeout`** (mandatory, in seconds) bounds the poll. It follows the same condition-wait rule as
  every other wait: no fixed sleep and no infinite poll.

**Determinism: the parts that matter.**

- **Only mail that arrives *after* the step starts counts.** The step records the set of **message
  ids** present when it began and ignores those ids. A stale code left in the mailbox by an earlier
  run is therefore never matched. Keying on id, rather than comparing the provider's `receivedAt` to
  the local run clock, is **skew-free**: it needs no agreement between the mailbox's clock and the
  runner's. A message with no provider id gets a stable content-hash id, so the baseline still
  distinguishes it.
- **A unique awaited message.** With the after-start boundary, the expected case is exactly one new
  matching message. If more than one matches, the **newest by `receivedAt`** wins, with a stable
  tie-break by message id. The result never depends on arrival-order races. `receivedAt` serves
  this ordering alone and is never the after-start gate.
- **Bounded poll.** The step re-fetches and re-checks until a match or the `timeout` deadline. This
  is the standard condition-wait pattern (repeat fetch and test), not a fixed sleep. Three cases are
  a **clean step failure**, never a silent wrong value: a timeout, a matched message whose body the
  `extract` regex does not hit, and a non-2xx fetch.

**Gate-testability.** The fetch sits behind an injectable HTTP function (the `http` step's client,
or a `RunFn`-style seam). Unit tests therefore cover the poll loop, the `after`-start filter, the
match/extract, the newest-wins tie-break, and the timeout over fabricated mailbox responses, with no
network. The live HTTP call is the only mocked surface, the permitted "external API call"
exception, exactly as `http` is tested.

## Alternatives considered

- **Disable 2FA / OTP in test builds and skip the side channel entirely.** This is the simplest
  option and fine for a smoke test, but it stops exercising the real authentication path, which is
  exactly the flow most worth covering. Rejected as the sole option. A build may still offer a test
  bypass, but the tool should not require one.
- **Mock the OTP/email provider with `mocks`.** `mocks` stubs the app's *own* outgoing traffic, so
  it works when the code arrives over a request the app makes and observes. It does not help when
  the value comes from an out-of-band channel (an authenticator app or a real inbox), which is the
  common 2FA case. Kept as a complement, not a replacement.
- **Reuse `http` (BE-0036) for everything.** `http` fetches a value from an endpoint. That covers a
  backend-issued token, but covers neither a locally computed TOTP nor a polled inbox. `http` stays
  for the HTTP case, and `totp` / `email` cover the rest. (`email` *reuses* `http`'s HTTP plumbing
  internally. It adds the polling, the match/extract, and the after-start determinism boundary that
  a one-shot `http` fetch cannot express.)
- **A provider-specific mailbox client (Mailosaur / MailSlurp SDK).** This is the quickest to wire
  for one provider, but it binds the tool to that vendor's SDK and API, and a second provider means
  a second client. Rejected in favour of the generic HTTP surface with configurable field paths. That
  surface reads any provider's JSON inbox with no added dependency. A provider that offers a
  non-HTTP API alone can still be fronted by a tiny bridge.
- **Poll a raw IMAP mailbox.** This works against a real inbox without a test-mail provider. It
  pulls in an IMAP client and credential handling, and parsing MIME for the body is its own surface.
  Deferred: the HTTP mailbox covers the test-provider case that the motivating 2FA flows use. An
  IMAP source can be added later behind the same `email` step contract if demand appears.

## Progress

- [x] `totp` step — computes an RFC 6238 code from a base32 secret into `${vars.*}` (`bajutsu/totp.py`); touches no device and no network.
- [x] `email` step — a bounded poll of `targets.<name>.mailbox` for a message arriving after the step started, matched and extracted into `${vars.*}` (`bajutsu/mailbox.py`, over an injected `MailboxReader`).

## References

Split out of the [BE-0036 HTTP utility step](../BE-0036-utility-steps/BE-0036-utility-steps.md).
[scenarios.md](../../docs/scenarios.md)
