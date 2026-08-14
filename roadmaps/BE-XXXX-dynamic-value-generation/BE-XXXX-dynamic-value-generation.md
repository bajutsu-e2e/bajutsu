**English** · [日本語](BE-XXXX-dynamic-value-generation-ja.md)

# BE-XXXX — Random and datetime value generation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-dynamic-value-generation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

A `generate` step computes a random or current-datetime value at run time and stores it as a
runtime variable, so a scenario can produce a fresh input rather than only referencing a literal,
a data-driven row, or a value captured from the UI.

## Motivation

Some flows need an input the author cannot write as a literal. A signup form refuses a username an
earlier run already took. A booking form takes today's date. A record needs a value guaranteed not
to collide with another scenario's.

Existing scenario mechanisms do not produce that kind of value. Data-driven rows
([BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md)) supply a fixed table
chosen in advance, and `extract` ([BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md))
captures a value the UI already displays — neither invents a value the scenario did not already
have. Without a way to generate one, an author works around the gap with a literal that later
collides, a date fixed at authoring time that ages, or a value seeded outside the scenario before
the run starts.

A step that generates the value in place — and hands it to later steps through the same `vars.*`
binding `extract`, `http`, and `totp` already use — closes that gap without adding a new
substitution mechanism.

## Detailed design

`generate` computes a value from one of two generator kinds and stores it under `vars.<var>`,
following the same run-time `vars.*` binding as `http`'s `saveBody`, in the `into: { var }` shape
`totp` already uses:

```yaml
- generate: { random: { string: { length: 8, charset: alnum } }, into: { var: username } }
- type: { text: "${vars.username}", into: { id: signup.username } }

- generate: { random: { uuid: {} }, into: { var: orderRef } }
- generate: { random: { int: { min: 1, max: 100 } }, into: { var: quantity } }

- generate: { datetime: { format: "%Y-%m-%d", offsetDays: 1 }, into: { var: tomorrow } }
- type: { text: "${vars.tomorrow}", into: { id: booking.date } }
```

**`random`** produces one of:

- **`string`** — `length` and an optional `charset` (`alnum` default, `alpha`, `numeric`, `hex`).
- **`int`** — an integer in `[min, max]`.
- **`float`** — a number in `[min, max]`, with an optional `precision` (decimal places).
- **`uuid`** — a version-4 UUID.

**`datetime`** produces the current time as text. An optional `format` field takes a `strftime`
pattern, defaulting to ISO 8601. An optional signed offset — `offsetSeconds`, `offsetMinutes`,
`offsetHours`, `offsetDays` — shifts that time for a relative value such as tomorrow or an hour
from now. An optional `timezone` field (an IANA name, for example `America/Los_Angeles`) computes
the value in that zone instead of the default, UTC. A scenario whose input must match a date the
app renders in the device's local zone should pass that zone explicitly, rather than rely on
the UTC default. Pinning the *device's* own zone to match is a
separate concern, tracked by
[BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive.md).

The run's evidence and report record each generated value, so a later failure shows which value the
step produced. A developer does not need a fixed seed to diagnose a run after the fact.

`generate` carries no `extract` modifier. It writes into `vars.*` through its own `into` field
instead, the same placement `totp` uses — so a reader finds every value-producing step in the same
shape.

Prime directives preserved:

- **No LLM on the run path.** Both generator kinds are a deterministic local computation — a
  pseudorandom-number generator (PRNG) draw, or a clock read. The run/CI verdict still comes
  only from machine-checkable assertions.
- **Determinism of flow, not of value.** With fields the validator accepted at load time, the
  step always executes and always succeeds — only the produced value varies between runs, the
  same way `totp`'s time-derived code already varies. An unresolvable `timezone` or an invalid
  `format` is rejected when the scenario loads, never silently substituted at run time. A
  scenario that must assert a specific value should capture it into `vars.*` and compare against
  that capture, not against a literal it could not have known in advance.
- **App-agnostic.** The step and its fields are identical across every target; nothing here is
  specific to one app.
- **Codegen.** `generate` runs in the bajutsu runner, not the app. It has no XCUITest, Playwright,
  or UI Automator equivalent, so codegen emits a labeled `// TODO` instead. This matches `http` and
  `totp` ([BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)).

## Alternatives considered

- **Function-call syntax inside `${...}` tokens (for example, `${random.int(1, 100)}`).**
  Rejected. `interp.py`'s tokens are a flat lookup into a precomputed bindings map, substituted the
  same way regardless of namespace. Parsing arguments out of a token would turn that primitive into
  a small expression language — the same alternative BE-0033 already rejected for control flow. A
  step keeps the same load-time/run-time split every other value-producing primitive already uses.
- **An optional `seed` field for fully reproducible random values.** Rejected for v1. Recording the
  generated value in the report already lets a developer see what ran. A scenario that needs a
  specific, predictable value should capture and compare it through `vars.*` rather than predict it
  from a seed. Worth revisiting if a concrete case needs bit-for-bit reproduction across runs.
- **Random selection from an in-scenario list, and a run-scoped incrementing counter.** Both are
  reasonable further generator kinds. Neither is needed to close the gap this item targets — a
  fresh, collision-resistant value with no advance knowledge. Left as future generator kinds under
  the same `generate` step, not as scope for this proposal.

## Progress

- [ ] Add the `generate` step schema (`random` / `datetime` generator kinds) to the scenario
  grammar and its validator.
- [ ] Implement the runner action handler, writing the produced value into `vars.<var>`.
- [ ] Record the generated value in evidence/report output.
- [ ] Emit the labeled codegen `// TODO` for each backend.
- [ ] Document `generate` in `scenarios.md` and `dsl-grammar.md` (English and Japanese).

## References

[scenarios.md](../../docs/scenarios.md), [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md)
(`http`/`totp`, the same run-time-computed-value pattern), [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md)
(`vars.*`, and the expression-language alternative already rejected there)
