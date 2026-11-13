**English** · [日本語](BE-XXXX-totp-named-generator-ja.md)

# BE-XXXX — Fetch a fresh TOTP code at any point from a named generator

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-totp-named-generator.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | TBD — filled in once the PR is opened |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

A scenario's `totp` step computes a time-based one-time password (TOTP) from a shared secret. It
follows Request for Comments (RFC) 6238 and writes the code into a runtime variable once. This
proposal lets a `totp` step register its secret under a name. A new `otp` step then fetches the
current code from that named generator. The `otp` step runs at any later point of the scenario and
never restates the secret.

## Motivation

A TOTP code is valid for one 30-second window of the clock. The `totp` step
([BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)) reads the clock once, when it
runs. It stores the code as a plain string in `${vars.<var>}`. Its `minRemaining` field
([BE-0474](../BE-0474-totp-window-alignment/BE-0474-totp-window-alignment.md)) keeps that one code
alive for the steps right after it. A scenario that needs a code later, or more than once, has to
repeat the whole `totp` step at each point:

- a sign-in flow that asks for a second code after a sensitive action, such as changing a password;
- a scenario that signs out and back in, and enters a code on each sign-in;
- a flow whose code field sits behind slow screens: a code generated up front expires before the
  scenario reaches the field.

Each repetition restates the `secret` field. An author must keep the secret out of the scenario
file and its evidence ([BE-0152](../BE-0152-totp-seed-artifact-leak/BE-0152-totp-seed-artifact-leak.md)).
Every extra copy is one more place to route through `${secrets.*}`. The repetition also hides
intent: a reader sees many independent generators where the flow has one authenticator.

Separating the two roles fixes both problems. The `totp` step owns the secret and registers it once
under a name. The `otp` step names that generator and fetches the code for the moment it runs. After
this change, a scenario that enters codes at many points carries the `secret` field once. Each `otp`
step writes the code of the window current at that step.

## Detailed design

### Registering a generator on the `totp` step

The `totp` step gains an optional `name` field. A step with a `name` registers its resolved secret
under that name. The registration lasts for the rest of the scenario run:

```yaml
- totp: { secret: "${secrets.TOTP_SEED}", name: main, into: { var: code } }
```

The `into` field becomes optional, so a step can register a generator without producing a code yet.
A `totp` step must still carry `name`, `into`, or both, since a step with neither does nothing. The
`minRemaining` field keeps its meaning but requires `into`. The window wait keeps a produced code
fresh, so a register-only step has nothing to wait for. The model refuses both shapes at load time.

The step checks the secret when it registers it. A malformed base32 secret fails the `totp` step
itself, not a later `otp` step. The message stays the same as today and never quotes the secret.
Registering a different secret under a name already in use fails the step, so two authenticators
given the same name by mistake cannot silently swap codes. Registering the same secret again
succeeds, so a `totp` step inside a `forEach` body still works.

### The `otp` step

The new `otp` step names a registered generator in `from` and writes its current code into `into`:

```yaml
- totp: { secret: "${secrets.TOTP_SEED}", name: main }
- tap: { id: auth.change_password }
# ... any number of steps ...
- otp: { from: main, into: { var: code }, minRemaining: 10 }
- type: { text: "${vars.code}", into: { id: auth.code } }
```

The `otp` step accepts the same `minRemaining` field as `totp`, with the same 1–30 bound. It runs
the same window wait, so its code outlives the steps that carry it to the app. The live log labels
the step `otp (≥10s left)`, the same way it labels a `totp` step that waits.

An `otp` step fails when its `from` names no generator registered so far. The step's message names
the missing generator. Registration follows execution order, not file order. A `totp` step inside
an `if` branch that did not run registers nothing.

### Where the registered secret lives

The run keeps a registered secret in the scenario's runtime bindings. That mapping already holds
the resolved `${secrets.*}` values. The key carries a `}` character, which the `${...}` token
grammar cannot contain, so no scenario token can reach the key. An author cannot read a registered
secret back through `${...}` and type it into the app. The generator lives as long as the scenario
run's bindings. The `if`, `forEach`, and `group` bodies share it, and the next scenario starts
with none.

The `otp` step carries no secret, so the existing evidence masking for `totp.secret` needs no
change. The scenario snapshot shows `from: main`. The produced code is the same short-lived value
that `totp` already writes into `${vars.*}`.

### Prime directives and codegen

- **No large language model (LLM) on the run path.** Both steps compute locally from the secret and
  the clock.
- **Determinism.** The `otp` step's only wait is the bounded `minRemaining` condition wait that
  `totp` already uses. A missing generator fails the step instead of producing a guessed value.
- **App-agnostic.** The steps run in the Bajutsu runner and behave identically on every backend.
- **Codegen.** Like `totp`, the `otp` step has no counterpart in a generated test. The XCUITest,
  UI Automator, and Playwright emitters share one helper that writes a labeled `// TODO` line. The
  line names the generator and the variable, never the secret.

## Alternatives considered

| Alternative | Why we rejected it |
|---|---|
| Repeat the `totp` step at each point | Works today, but restates the secret at every point and hides that the flow has one authenticator. |
| Re-generate into the same variable, keyed by the variable the `totp` step wrote | Ties the generator to one variable name. A flow that keeps two codes, such as an old and a new one, cannot express both. |
| A lazy token such as `${totp.main}` that computes the code at expansion time | No step marks when the clock is read, so the report cannot show when the code was taken, and `minRemaining` has no step to attach its wait to. |
| Store the secret under an addressable key, such as `totp.main` | A `${totp.main}` token would then splice the raw secret into a typed text field, which the evidence masking for literal secrets does not cover. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `totp` model: optional `name`, optional `into`, and the two shape checks
- [x] `otp` model, step registration, and the generator lookup in the run loop
- [x] `minRemaining` window wait and live-log label for `otp`
- [x] Codegen `// TODO` lines for `otp`, and the `into`-less `totp`
- [x] Docs (English and Japanese scenario reference) and tests

## References

- [BE-0046 — OTP & email side-channel steps](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)
  — the `totp` step this proposal extends.
- [BE-0474 — Wait for a TOTP window with enough time left](../BE-0474-totp-window-alignment/BE-0474-totp-window-alignment.md)
  — the `minRemaining` wait the `otp` step reuses.
- [BE-0152 — TOTP seed artifact leak](../BE-0152-totp-seed-artifact-leak/BE-0152-totp-seed-artifact-leak.md)
  — the evidence masking this proposal leaves unchanged.
- [RFC 6238](https://datatracker.ietf.org/doc/html/rfc6238) — TOTP: Time-Based One-Time Password
  Algorithm.
