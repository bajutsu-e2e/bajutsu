**English** · [日本語](BE-0082-capability-preflight-check-ja.md)

# BE-0082 — Preflight capability check before a run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0082](BE-0082-capability-preflight-check.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0082") |
| Implementing PR | [#229](https://github.com/bajutsu-e2e/bajutsu/pull/229) |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

This item adds a **preflight check** to `run`. At the start of a run, before any device work, the
check scans the scenario for the capabilities its actions and assertions require. It compares them
against `driver.capabilities()` and, if anything is unsupported, fails immediately and
deterministically with a clear, aggregated message.

Every backend declares what it can do through `Driver.capabilities()`. That call returns a set of
capability tokens (`query`, `semanticTap`, `conditionWait`, `network`, `screenshot`, `elements`,
`multiTouch`) defined in `bajutsu/drivers/base.py`. A scenario, however, can ask for an action the
chosen backend does not support. Examples are a two-finger pinch on idb (single-touch only) and a
`request` assertion on a backend with no `network` capability.

Today the runner checks only one such mismatch, and only at the moment the action runs:
`gestures.py` calls `_require_multi_touch` mid-run and raises `UnsupportedAction`. A scenario whose
*last* step needs an unsupported capability therefore runs every earlier step on a device first,
then fails late.

The preflight is a determinism-and-diagnostics improvement to the deterministic `run`/CI path, and
no LLM is involved. It serves prime directive #2 (determinism first): fail fast and clearly rather
than do partial work and surface the problem late.

## Motivation

- **The failure surfaces late and per-action.** `_require_multi_touch` is the only capability gate,
  and it fires when the gesture executes. Suppose a scenario taps through five screens and only
  then does an unsupported pinch. The run pays for five screens of device work before failing. The
  failure report also looks like a mid-run error, not like "this scenario can't run on this
  backend".
- **The mismatch is knowable before the device is touched.** A scenario's steps and assertions are
  fully known up front, and so is the backend's capability set. The check needs no device state, so
  it can run as a pure preflight. A check of this kind should fail before the run, not during it.
- **The check is per-backend, and the backend matrix is growing.** iOS (idb) and Web (Playwright)
  exist today, and Android is planned
  ([BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)).
  Capability gaps between backends are therefore a structural, recurring fact, not a one-off. A
  scenario authored against one backend and run on another is where a clear "unsupported on this
  backend" message earns its keep. BE-0009's per-platform table already centers `capabilities()` as
  the backend contract.
- **The current capability surface is under-modeled.** Only `multiTouch` is actually checked. The
  mapping from "scenario construct → required capability" is implicit and incomplete. A `request` /
  `event` / `requestSequence` assertion needs `network`, and a condition wait may want
  `conditionWait`. Making that mapping explicit is the substance of this item.

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

The exact rows are settled during implementation against the current action/assertion set. The
table is the contract that the preflight enforces.

### The preflight

At run start, after the driver is selected but before the first action, the runner walks the
resolved scenario. The walk includes expanded shared/parameterized steps and data-driven rows, so
the check sees exactly what will execute. The runner collects the set of required capabilities and
diffs it against `driver.capabilities()`.

If the difference is non-empty, the run fails immediately with one aggregated error. The error
names **every** unsupported construct and the backend it was checked against. It is one error for
the whole scenario, not one per action, and it arrives before any device work. The existing
`UnsupportedAction` exception type (today raised by `_require_multi_touch`) is reused so the
failure classifies consistently in the report.

`_require_multi_touch` stays as a defense-in-depth assertion, because the invariant should already
hold by the time a gesture runs. The preflight, not `_require_multi_touch`, becomes the *primary*
gate.

### Determinism

The check is a pure function of (scenario, capability set), with no device, no clock, and no
network. It passes or fails the same way every time, and it fails *before* any non-deterministic
device interaction. The check strengthens the determinism guarantee.

### One open question to confirm during implementation

`base.py`'s `Capability` tokens are only partially consumed today (only `multiTouch` is checked, in
`gestures.py`). Implementation must first audit which constructs genuinely depend on which
capability, because some assertions may degrade gracefully rather than hard-require a capability.
The audit lets the map gate only true hard requirements, so it does not reject scenarios that would
actually run.

## Alternatives considered

- **Keep checking per-action at run time.** Rejected as the weakness of the status quo: the run does
  partial device work before failing and reports late. Preflight performs the same machine check
  earlier, which is strictly better.
- **A static lint in a separate `validate`/`lint` command, not in `run`.** Useful as a complement.
  The check must still live in `run` itself, to guarantee that no scenario ever does partial device
  work on an unsupported action. A separate lint can reuse the same map later.
- **Let the backend silently no-op or approximate an unsupported action.** Rejected outright, because
  it violates determinism first. An approximated single-touch "pinch" that silently passes is the
  "tap whatever matched" failure mode that the prime directives forbid. Failing clearly is the
  point.
- **Model capabilities at the config layer instead.** Capabilities are a property of the backend
  implementation, not per-app config. Modeling them in config would duplicate what the driver
  reports and risk drifting from it. `driver.capabilities()` stays the single source of truth.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- `bajutsu/drivers/base.py` (`Capability`, `Driver.capabilities()`) — the capability contract this
  enforces; `bajutsu/orchestrator/actions/handlers/gestures.py` (`_require_multi_touch`) — the
  existing, narrower, run-time check this generalizes.
- [BE-0009 — Cross-platform abstractions](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
  — the per-platform backend matrix where `capabilities()` is the backend contract; this item makes
  the runner enforce it up front.
- [CLAUDE.md](../../CLAUDE.md) — prime directive #2 (determinism first: fail fast rather than
  guess or do partial work).
