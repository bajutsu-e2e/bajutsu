**English** · [日本語](BE-0157-shake-device-primitive-ja.md)

# BE-0157 — Shake device primitive

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0157](BE-0157-shake-device-primitive.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal (deferred)** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0157") |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Related | [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

Triggering the **shake** gesture — one of the device-state primitives
[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
proposed. BE-0052's implementation triage found no reliable, deterministic, headless actuator for
it, so it moves here as its own deferred item, separate from the timezone primitive (a different
blocker, tracked in its own item), to wait on a verified mechanism.

## Motivation

Some apps bind "undo" or a debug menu to the shake gesture, and there is no way to trigger it today.
Closing this gap would remove an easy "Maestro can, Bajutsu can't" objection, since Maestro ships a
broad device-control vocabulary that Bajutsu already matches for most of BE-0035's primitives.

What blocks this is not authoring effort but the absence of a deterministic actuator that runs
headlessly. A primitive that only works with a GUI window open and a manually granted permission is
a fundamentally different kind of primitive than the rest of the device-control surface, which runs
against a plain `simctl boot`ed device with no GUI. Recording precisely what is missing keeps the
door open for a future headless mechanism, without forcing an adoption decision on the GUI-dependent
path today.

## Detailed design

The proposed surface is unchanged from BE-0052:

```yaml
- shake: {}                                   # the shake gesture
```

**Why it is blocked.** There is no headless actuator for the shake gesture:

- `simctl` has no `shake` subcommand; `idb` has no shake command either. Shake is a Simulator GUI
  menu item (Device ▸ Shake).
- It can be triggered by **GUI automation** — AppleScript / System Events clicking the menu, or a
  third-party tool such as RocketSim. This path is deterministic (no LLM), so it does not violate
  prime directive #1. But it requires the Simulator **GUI app to be running** and an
  **Accessibility grant** for the controlling process, is fragile to menu localization and layout,
  acts on the *focused* Simulator (non-deterministic across multiple booted devices), and
  **cannot run in headless CI** (`simctl boot` without the GUI). RocketSim specifically does not
  expose shake or timezone through its CLI (only tap/swipe/type/button/inspect), needs its own Mac
  app plus Accessibility, and would add a paid, closed-source dependency on the `run` path — so it
  does not improve on plain AppleScript for this.

A viable mechanism would have to trigger shake headlessly (no GUI, no Accessibility grant) and
target a specific device. None is known today. If shake is ever adopted through GUI automation as an
explicit, opt-in, local-only escape hatch, it must fail cleanly where the GUI or Accessibility grant
is absent (like the existing device-control steps on the fake driver and in parallel runs), and it
must be documented as unavailable in headless CI.

**Codegen.** Shake remains `simctl`-less (or GUI-only), so — like the other device-control steps and
consistent with [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)
— a future implementation would emit a labeled `// TODO` naming the command rather than a faithful
XCUITest step.

**Prime directives.** A future implementation must keep the `run`/CI gate AI-free (directive #1),
actuate deterministically with a machine-checkable result and no settle-sleep (directive #2), and
introduce no per-app code (directive #3).

## Alternatives considered

- **Ship via AppleScript / RocketSim GUI automation now.** Deferred rather than adopted: it needs
  the Simulator GUI plus an Accessibility grant, is unavailable in headless CI, and (for RocketSim)
  adds a paid third-party dependency on the `run` path. If taken up later it must be an explicit,
  clean-failing, local-only escape hatch, not a headless primitive.
- **Approximate it from inside the app (a debug deeplink that simulates a shake).** Rejected as the
  primary mechanism: it pushes the burden onto every target app and breaks app-agnosticism. Launch
  env remains available for genuinely app-specific setup.
- **Drive the system through the alert-guard vision path (screenshot + tap).** Rejected: that path
  is an AI fallback and must never enter the deterministic run gate (prime directive #1).

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] A verified headless shake actuator that targets a specific device (or an explicit, clean-failing, local-only GUI-automation escape hatch, documented as CI-unavailable).

Carved out of [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
during its implementation triage; waits on a verified mechanism.

## References

Split out of [BE-0052 — Device-state primitives: timezone, clipboard, shake](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md),
itself split from [BE-0035 — Device-control steps](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md).
[DESIGN §6.2](../../DESIGN.md), `bajutsu/orchestrator/actions/handlers/device.py`, `bajutsu/env.py`
