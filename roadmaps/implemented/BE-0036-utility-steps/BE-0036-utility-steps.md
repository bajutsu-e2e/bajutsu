**English** · [日本語](BE-0036-utility-steps-ja.md)

# BE-0036 — HTTP utility step

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0036](BE-0036-utility-steps.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | [#58](https://github.com/bajutsu-e2e/bajutsu/pull/58) |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

The `http` step issues an HTTP request from the runner — for test-data setup, webhook triggers,
or fetching a value a later step needs — and saves the response body into the `vars.*` namespace.
The OTP/2FA and email side-channel steps this item was originally scoped alongside are split out
to a separate proposal (see [References](#references)).

## Motivation

A run often depends on a value the UI cannot supply by itself: a token from a backend, a fixture
seeded over an API, a webhook the run must trigger. Without a way to make a request and bring its
result into the run, those cases fall back to manual setup (not reproducible) or are skipped.
`http` closes that gap: a scenario fetches the value and feeds it into a later step as a runtime
variable, deterministically.

## Detailed design

`http` issues a request from the runner process (not the app) and stores the response body as a
runtime variable for later `${vars.*}` interpolation:

```yaml
- http: { method: POST, url: "${secrets.API}/login", body: "...", status: 200, saveBody: token }
- type: { text: "${vars.token}", into: { id: auth.token } }
```

Fields and contract:

- **`method`** (default `GET`), **`url`** (must be `http`/`https`), optional **`headers`** and
  **`body`**.
- **`status`** — if given, the response status is checked against it; a mismatch fails the step.
- **`saveBody`** — stores the **whole** response body text as `vars.<saveBody>`. (Extracting a
  single field by JSON path or regex, rather than saving the whole body, is a natural follow-up
  and is noted as future work, not yet implemented.)
- It is a *fetch utility*, distinct from the in-app `request` assertion / `mocks`, which observe
  the app's own traffic. The same PR also added the device-reset helpers `clearKeychain` and
  `clearClipboard`.

Prime directives preserved:

- **No LLM on the run path.** `http` is a deterministic request whose result is captured by a
  machine rule (status check, body capture). The pass/fail judgment still comes only from
  machine-checkable assertions; the run/CI gate stays AI-free.
- **Determinism.** The request is immediate; a failed request or a status mismatch surfaces as a
  clean step failure, not a silent wrong value.
- **App-agnostic.** The step and its wiring are identical across apps; only the endpoints and
  secrets (config / `${secrets.*}`) differ. Secrets (API base URLs, tokens) are referenced as
  `${secrets.X}` and auto-masked in evidence, so the scenario stores tokens, never values.
- **Codegen.** `http` runs in the bajutsu runner, not the app, and has no XCUITest equivalent, so
  codegen emits a labeled `// TODO` (per
  [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)).

## Alternatives considered

- **A general `shell`/`exec` step that runs an arbitrary command to produce the value.** Maximum
  flexibility, but it makes scenarios non-portable and non-reviewable (anything can run) and
  couples the test to the CI host. Rejected in favor of a few purpose-built, auditable steps.
- **Observe the value with `mocks` / the `request` assertion instead.** Those stub or observe the
  app's *own* traffic; they do not help when the value must be fetched out of band by the test
  itself. Kept as complementary, not a replacement.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

The OTP/2FA and email side-channel steps (`totp`, `email`) are tracked separately in
[OTP &amp; email side-channel steps](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md).

[scenarios.md](../../../docs/scenarios.md), `bajutsu/orchestrator/actions/handlers/http.py`
