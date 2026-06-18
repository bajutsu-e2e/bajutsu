**English** · [日本語](BE-0036-utility-steps-ja.md)

# BE-0036 — Utility steps

* Proposal: [BE-0036](BE-0036-utility-steps.md)
* Status: **Proposal**
* Track: [Proposals](../README.md#proposals)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: MagicPod

## Introduction

Issue HTTP requests / generate OTP (one-time password) / 2FA (two-factor authentication) codes / verify received email via APIs. Needed for automating real-app login flows.

## Motivation

A real-app login flow is rarely just typing a password. It commonly requires a side channel the
UI cannot supply by itself: a one-time password (OTP) from an authenticator, a two-factor
authentication (2FA) code delivered by email or SMS, or a token obtained from a backend HTTP
call. Without a way to bring those values into the run, the most important flows — sign-in,
account verification, password reset — can't be automated deterministically. Authors are forced
either to disable 2FA in test builds (which then no longer exercises the real path) or to paste
a value by hand (which isn't reproducible). Utility steps close that gap by letting a scenario
fetch the side-channel value and feed it into a later step as a runtime variable.

## Detailed design

Add a small family of steps that produce a value into the existing `${vars.*}` namespace
([scenarios](../../scenarios.md#runtime-variables-vars)), so a later `type` / `assert` can
consume it deterministically:

```yaml
- http: { method: GET, url: "${secrets.MAIL_API}/latest", into: { var: code, jsonPath: "$.otp" } }
- type: { text: "${vars.code}", into: { id: auth.otp } }
```

```yaml
- totp: { secret: "${secrets.TOTP_SEED}", into: { var: code } }   # RFC 6238 time-based OTP
- type: { text: "${vars.code}", into: { id: auth.code } }
```

```yaml
- email: { match: { to: "test@example.com", subjectMatches: "code" }, extract: { var: code, bodyMatches: "[0-9]{6}" }, timeout: 30 }
```

Mapping and contract:

- **`http`** issues a request from the runner process and extracts a field (a JSON path or a
  regex over the body) into `vars`. It is a *fetch utility*, distinct from the in-app `request`
  assertion / `mocks`, which observe the app's own traffic.
- **`totp`** computes a time-based one-time password locally from a seed (RFC 6238). Pure
  computation, fully deterministic given the seed and the clock, no network.
- **`email`** polls a test mailbox API until a matching message arrives, then extracts the code.
  The wait is a **condition wait with a mandatory `timeout`** (the same rule as every other
  wait — no fixed sleep, no infinite poll); a timeout is a step failure.

Secrets (API base URLs, TOTP seeds, mailbox credentials) are referenced as `${secrets.X}` and
declared in config, so the scenario file stores tokens, never values, and they are auto-masked
in evidence. Endpoint configuration that is shared across scenarios lives in `apps.<name>`.

Prime directives preserved:

- **No LLM on the run path.** Every utility step is deterministic computation or a deterministic
  request/match — `totp` is arithmetic, `http`/`email` extract by JSON path or regex. The
  pass/fail judgment still comes only from machine-checkable assertions; the run/CI gate stays
  AI-free.
- **Determinism.** `email` waits on a condition with a bounded timeout, never a sleep. `http`
  and `totp` are immediate. A flaky external mailbox surfaces as a clean step failure, not a
  silent wrong value.
- **App-agnostic.** The steps and their wiring are identical across apps; only the endpoints and
  secrets (config / `${secrets.*}`) differ.
- **Codegen.** These run in the bajutsu runner, not the app, and have no XCUITest equivalent, so
  codegen emits a labeled `// TODO` (per
  [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)).

## Alternatives considered

- **Disable 2FA / OTP in test builds and skip the side channel entirely.** Simplest, and fine
  for a smoke test, but it stops exercising the real authentication path — exactly the flow most
  worth covering. Rejected as the only option; a build may still offer a test bypass, but the
  tool should not require it.
- **Mock the OTP/email provider with `mocks`.** `mocks` stubs the app's *own* outgoing traffic,
  so it works when the code arrives over a request the app makes and observes. It does not help
  when the value comes from an out-of-band channel (an authenticator app, a real inbox), which
  is the common 2FA case. Kept as complementary, not a replacement.
- **A general `shell`/`exec` step that runs an arbitrary command to produce the value.** Maximum
  flexibility, but it makes scenarios non-portable and non-reviewable (anything can run) and
  couples the test to the CI host. Rejected in favor of a few purpose-built, auditable steps.

## References

[scenarios.md](../../scenarios.md)
