**English** · [日本語](BE-0046-otp-email-steps-ja.md)

# BE-0046 — OTP & email side-channel steps

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0046](BE-0046-otp-email-steps.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
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
  [BE-0026](../../implemented/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)).

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
  case; `totp` / `email` cover the rest.

## References

Split out of [BE-0036 — HTTP utility step](../../implemented/BE-0036-utility-steps/BE-0036-utility-steps.md).
[scenarios.md](../../../docs/scenarios.md)
