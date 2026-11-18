**English** · [日本語](BE-0377-dynamic-value-generation-ja.md)

# BE-0377 — Random and datetime value generation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0377](BE-0377-dynamic-value-generation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0377") |
| Implementing PR | [#1649](https://github.com/bajutsu-e2e/bajutsu/pull/1649) |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

A `generate` step computes a random or current-datetime value at run time and stores it as a
runtime variable. A scenario can then produce a fresh input. Before this step, a scenario could
reference nothing but a literal, a data-driven row, or a value captured from the UI.

## Motivation

Some flows need an input the author cannot write as a literal. A signup form refuses a username
that an earlier run already took. A booking form takes today's date. A record needs a value
guaranteed not to collide with another scenario's.

Existing scenario mechanisms do not produce such a value. Data-driven rows
([BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios.md)) supply a fixed table
chosen in advance. `extract` ([BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md))
captures a value the UI already displays. Neither invents a value the scenario did not already
have.

Without a way to generate one, an author works around the gap in one of three ways:

- a literal that later collides
- a date fixed at authoring time that goes stale
- a value seeded outside the scenario before the run starts

A step that generates the value in place closes the gap. It hands the value to later steps through
the same `vars.*` binding that `extract`, `http`, and `totp` already use. No new substitution
mechanism is needed.

## Detailed design

`generate` computes a value from one of two generator kinds and stores it under `vars.<var>`. The
binding is the run-time `vars.*` binding that `http`'s `saveBody` uses, in the `into: { var }`
shape that `totp` already uses:

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
- **`uuid`** — a version-4 universally unique identifier (UUID).

**`datetime`** produces the current time as text. Its optional fields are:

- **`format`**: a `strftime` pattern. The default is ISO 8601, the date and time format of the
  International Organization for Standardization (ISO).
- **`offsetSeconds`, `offsetMinutes`, `offsetHours`, `offsetDays`**: a signed offset that shifts
  the time, for a relative value such as tomorrow or an hour from now.
- **`timezone`**: an Internet Assigned Numbers Authority (IANA) name, for example
  `America/Los_Angeles`. The value is computed in that zone instead of the default, Coordinated
  Universal Time (UTC).

A scenario whose input must match a date the app renders in the device's local zone should pass
that zone explicitly. The UTC default does not match it. Pinning the *device's* own zone is a
separate concern, tracked by
[BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive.md).

The run's evidence and report record each generated value, so a later failure shows which value the
step produced. A developer does not need a fixed seed to diagnose a run after the fact.

`generate` carries no `extract` modifier. It writes into `vars.*` through its own `into` field,
the same placement `totp` uses. A reader then finds every value-producing step in the same shape.

Prime directives preserved:

- **No large language model (LLM) on the run path.** Both generator kinds are a deterministic local
  computation: a pseudorandom-number generator (PRNG) draw, or a clock read. The run/CI verdict
  still comes from machine-checkable assertions alone.
- **Determinism of flow, not of value.** The validator checks the fields when the scenario loads.
  A step with accepted fields always executes and always succeeds. The produced value alone
  varies between runs, as `totp`'s time-derived code already does. An unresolvable `timezone` or
  a malformed `format` is rejected at load time. The runner never substitutes another value at
  run time. A scenario that must assert a specific value captures it into `vars.*` and compares
  against that capture. It cannot compare against a literal it could not have known in advance.
- **App-agnostic.** The step and its fields are identical across every target. Nothing here is
  specific to one app.
- **Codegen.** `generate` runs in the bajutsu runner, not the app. XCUITest, Playwright, and
  UI Automator have no counterpart for it, so codegen emits a labeled `// TODO` instead. This
  matches `http` and `totp` ([BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)).

## Alternatives considered

- **Function-call syntax inside `${...}` tokens (for example, `${random.int(1, 100)}`).**
  Rejected. The tokens in `interp.py` are a flat lookup into a precomputed bindings map. The map
  substitutes them the same way regardless of namespace. Parsing arguments out of a token would
  turn the lookup into a small expression language, the same alternative BE-0033 already rejected
  for control flow. A step keeps the load-time/run-time split that every other value-producing
  step uses.
- **An optional `seed` field for fully reproducible random values.** Rejected for v1. Recording
  the generated value in the report already lets a developer see what ran. A scenario that needs
  a specific, predictable value captures and compares it through `vars.*` instead of predicting
  it from a seed. A concrete case that needs bit-for-bit reproduction across runs would justify
  revisiting this.
- **Random selection from an in-scenario list, and a run-scoped incrementing counter.** Both are
  reasonable further generator kinds. This item targets a fresh, collision-resistant value with
  no advance knowledge, and neither kind is needed for that. They stay future generator kinds
  under the same `generate` step, outside the scope of this proposal.

## Progress

- [x] Add the `generate` step schema (`random` / `datetime` generator kinds) to the scenario
  grammar and its validator.
- [x] Implement the runner action handler, writing the produced value into `vars.<var>`.
- [x] Record the generated value in evidence/report output.
- [x] Emit the labeled codegen `// TODO` for each backend.
- [x] Document `generate` in `scenarios.md` and `dsl-grammar.md` (English and Japanese).

## References

[scenarios.md](../../docs/scenarios.md), [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md)
(`http`/`totp`, the same run-time-computed-value pattern), [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md)
(`vars.*`, and the expression-language alternative already rejected there)
