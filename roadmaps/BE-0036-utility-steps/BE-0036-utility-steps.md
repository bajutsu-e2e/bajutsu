**English** · [日本語](BE-0036-utility-steps-ja.md)

# BE-0036 — HTTP utility step

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0036](BE-0036-utility-steps.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0036") |
| Implementing PR | [#58](https://github.com/bajutsu-e2e/bajutsu/pull/58) |
| Topic | Scenario authoring features |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

The `http` step issues an HTTP request from the runner and saves the response body into the `vars.*` namespace. A scenario uses it for test-data setup, for webhook triggers, or to fetch a value that a later step needs. The item was first scoped alongside the one-time password (OTP) / two-factor authentication (2FA) and email side-channel steps. Those steps moved to a separate proposal (see [References](#references)).

## Motivation

A run often depends on a value that the UI cannot supply by itself: a token from a backend, a fixture seeded over an API, or a webhook that the run must trigger. Without a way to make a request and bring its result into the run, these cases fall back to manual setup, which is not reproducible, or are skipped. The `http` step closes that gap. A scenario fetches the value and deterministically feeds it into a later step as a runtime variable.

## Detailed design

The `http` step issues a request from the runner process, not from the app. It stores the response body as a runtime variable for later `${vars.*}` interpolation:

```yaml
- http: { method: POST, url: "${secrets.API}/login", body: "...", status: 200, saveBody: token }
- type: { text: "${vars.token}", into: { id: auth.token } }
```

Fields and contract:

- **`method`** (default `GET`), **`url`** (must be `http` or `https`), and optional **`headers`** and **`body`**.
- **`status`**: if given, the step checks the response status against it, and a mismatch fails the step.
- **`saveBody`**: stores the **whole** response body text as `vars.<saveBody>`. Extracting a single field by JSON path or regex, instead of saving the whole body, is a natural follow-up. It is noted as future work and is not yet implemented.
- The step is a *fetch utility*. It differs from the in-app `request` assertion and from `mocks`, which observe the app's own traffic. The same PR also added the device-reset helpers `clearKeychain` and `clearClipboard`.

Prime directives preserved:

- **No LLM on the run path.** The `http` step is a deterministic request, and a machine rule captures its result (status check, body capture). The pass/fail judgment still comes from machine-checkable assertions alone, so the run/CI gate stays AI-free.
- **Determinism.** The request is immediate. A failed request or a status mismatch surfaces as a clean step failure, never as a silent wrong value.
- **App-agnostic.** The step and its wiring are identical across apps. Only the endpoints and secrets (config / `${secrets.*}`) differ. A scenario references secrets such as API base URLs and tokens as `${secrets.X}`, and evidence masks them automatically. The scenario therefore stores tokens, never values.
- **Codegen.** The `http` step runs in the bajutsu runner, not in the app, and has no XCUITest equivalent. Codegen therefore emits a labeled `// TODO` (per [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)).

## Alternatives considered

- **A general `shell` / `exec` step that runs an arbitrary command to produce the value.** This gives maximum flexibility. It also makes scenarios non-portable and non-reviewable, because anything can run, and it couples the test to the CI host. Rejected in favor of a few purpose-built, auditable steps.
- **Observe the value with `mocks` or the `request` assertion instead.** Those features stub or observe the app's *own* traffic. They do not help when the test itself must fetch the value out of band. Kept as a complement, not a replacement.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

The OTP/2FA and email side-channel steps (`totp`, `email`) are tracked separately in [OTP &amp; email side-channel steps](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md).

[scenarios.md](../../docs/scenarios.md), `bajutsu/orchestrator/actions/handlers/http.py`
