**English** · [日本語](BE-0082-capability-preflight-check-ja.md)

# BE-0082 — Preflight capability check before a run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0082](BE-0082-capability-preflight-check.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

Every backend declares what it can do through `Driver.capabilities()` — a set of capability
tokens (`query`, `semanticTap`, `conditionWait`, `network`, `screenshot`, `elements`,
`multiTouch`) defined in `bajutsu/drivers/base.py`. A scenario, however, can ask for an action the
chosen backend does not support: a two-finger pinch on idb (single-touch only), or a `request`
assertion on a backend with no `network` capability. Today only one such mismatch is checked, and
only at the moment the action runs — `gestures.py` calls `_require_multi_touch` mid-run and raises
`UnsupportedAction`. So a scenario whose *last* step needs an unsupported capability runs every
earlier step on a device first, then fails late. This item adds a **preflight check**: at the start
of a run, before any device work, scan the scenario for the capabilities its actions and assertions
require, compare them against `driver.capabilities()`, and fail immediately and deterministically
with a clear, aggregated message if anything is unsupported.

This is a determinism-and-diagnostics improvement to the deterministic `run`/CI path — no LLM is
involved — and it directly serves prime directive #2 (determinism first: fail fast and clearly
rather than do partial work and surface the problem late).

## Motivation

- **The failure surfaces late and per-action.** `_require_multi_touch` is the only capability gate,
  and it fires when the gesture executes. A scenario that taps through five screens and only then
  does an unsupported pinch pays for five screens of device work before failing — wasted time, and a
  failure report that looks like a mid-run error rather than "this scenario can't run on this
  backend".
- **The mismatch is knowable before the device is touched.** A scenario's steps and assertions are
  fully known up front, and so is the backend's capability set. The check needs no device state, so
  it can run as a pure preflight — exactly the kind of thing that should fail before, not during, the
  run.
- **It is per-backend, and the backend matrix is growing.** With iOS (idb), Web (Playwright), and
  Android planned ([BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)),
  capability gaps between backends are a structural, recurring fact, not a one-off. A scenario
  authored against one backend and run on another is precisely where a clear "unsupported on this
  backend" message earns its keep. (BE-0009's per-platform table already centers `capabilities()` as
  the backend contract.)
- **The current capability surface is under-modeled.** Only `multiTouch` is actually checked. The
  mapping from "scenario construct → required capability" is implicit and incomplete: a `request` /
  `event` / `requestSequence` assertion needs `network`, a condition wait may want `conditionWait`,
  and so on. Making that mapping explicit is the substance of this item.

## Detailed design

### The capability requirement map

Define, in one place, the mapping from each scenario construct to the capability it requires:

| Scenario construct | Required capability |
|---|---|
| `pinch` / `rotate` (two-finger gestures) | `multiTouch` |
| `request` / `event` / `requestSequence` / `responseSchema` assertion | `network` |
| `until: { request }` wait | `network` |
| `visual` assertion / screenshot capture | `screenshot` |
| every run (baseline) | `query`, `elements` |

(The exact rows are settled during implementation against the current action/assertion set; the
table is the contract the preflight enforces.)

### The preflight

At run start, after the driver is selected but before the first action, the runner walks the
resolved scenario (including expanded shared/parameterized steps and data-driven rows, so the check
sees exactly what will execute), collects the set of required capabilities, and diffs it against
`driver.capabilities()`. If the difference is non-empty, the run fails immediately with one
aggregated error naming **every** unsupported construct and the backend it was checked against —
not one error per action, and not after partial device work. The existing `UnsupportedAction`
exception type (today raised by `_require_multi_touch`) is reused so the failure classifies
consistently in the report.

`_require_multi_touch` stays as a defense-in-depth assertion (the invariant should already hold by
the time a gesture runs), but it is no longer the *primary* gate — the preflight is.

### Determinism

The check is a pure function of (scenario, capability set): no device, no clock, no network. It
either passes or fails the same way every time, and it fails *before* any non-deterministic device
interaction — strengthening, not bending, the determinism guarantee.

### One open question to confirm during implementation

`base.py`'s `Capability` tokens are only partially consumed today (only `multiTouch` is checked, in
`gestures.py`). Implementation must first audit which constructs genuinely depend on which
capability — some assertions may degrade gracefully rather than hard-require a capability — so the
map gates only true hard requirements and does not reject scenarios that would actually run.

## Alternatives considered

- **Keep checking per-action at run time.** Rejected as the status quo's weakness: it does partial
  device work before failing and reports late. Preflight is strictly better for the same machine
  check, moved earlier.
- **A static lint in a separate `validate`/`lint` command, not in `run`.** Useful as a complement,
  but the check must be in `run` itself to guarantee no scenario ever does partial device work on an
  unsupported action. A separate lint can reuse the same map later.
- **Let the backend silently no-op or approximate an unsupported action.** Rejected outright: it
  violates determinism first — an approximated single-touch "pinch" that silently passes is exactly
  the "tap whatever matched" failure mode the prime directives forbid. Failing clearly is the point.
- **Model capabilities at the config layer instead.** Capabilities are a property of the backend
  implementation, not per-app config; modeling them in config would duplicate and risk drifting from
  what the driver actually reports. `driver.capabilities()` stays the single source of truth.

## References

- `bajutsu/drivers/base.py` (`Capability`, `Driver.capabilities()`) — the capability contract this
  enforces; `bajutsu/orchestrator/actions/handlers/gestures.py` (`_require_multi_touch`) — the
  existing, narrower, run-time check this generalizes.
- [BE-0009 — Cross-platform abstractions](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
  — the per-platform backend matrix where `capabilities()` is the backend contract; this item makes
  the runner enforce it up front.
- [CLAUDE.md](../../../CLAUDE.md) — prime directive #2 (determinism first: fail fast rather than
  guess or do partial work).
