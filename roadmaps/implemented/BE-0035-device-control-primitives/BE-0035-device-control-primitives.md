**English** · [日本語](BE-0035-device-control-primitives-ja.md)

# BE-0035 — Device-control steps (background, status-bar override)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0035](BE-0035-device-control-primitives.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | [#59](https://github.com/bajutsu-e2e/bajutsu/pull/59) |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

Three deterministic device-control steps that drive the Simulator outside the element tree:
`background` (send the app to the background), `overrideStatusBar` (pin the status bar for
stable screenshots), and `clearStatusBar` (restore the live status bar). The remaining
device-state primitives this item was originally scoped for — timezone, clipboard seeding, and
shake — are split out to a separate proposal (see [References](#references)).

## Motivation

Real flows depend on device state the app does not control, and a deterministic side effect on
the device — evaluated outside the element tree, with no AI — is the only way to set it up
reproducibly. Two such needs shipped here:

- **Foreground/background transitions.** A lot of behavior fires only on backgrounding
  (state restoration, a re-auth prompt, a refresh-on-resume). `relaunch` restarts the app, which
  is a different event; backgrounding the app via the Home button is its own case.
- **Deterministic status bar.** Visual-regression assertions ([BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md))
  compare screenshots, and a live status bar (clock, battery, signal) makes every capture differ.
  Pinning the status bar to fixed values removes that source of flakiness; clearing it restores
  the live one afterward.

## Detailed design

Each step drives the Simulator through the existing `simctl` / device-control channel, following
the pattern `setLocation` and `push` already established: a deterministic side effect, no AI,
evaluated outside the element tree.

```yaml
- background: {}                                    # send the app to the background (Home button)
- overrideStatusBar: { time: "9:41", batteryLevel: 100, cellularBars: 4, wifiBars: 3 }
- clearStatusBar: {}                                # restore the live status bar
```

Mapping to the backend:

- **`background`** sends the app to the background by pressing Home (`simctl ui home` via the
  injected device control's `home()`). It takes no fields — it is the backgrounding action only.
  Resuming the app (and a bounded backgrounded interval) is future work in the split-out proposal.
- **`overrideStatusBar`** overrides only the fields the step provides — `time`, `batteryLevel`,
  `batteryState`, `cellularBars`, `wifiBars` — mapping to `simctl status_bar override`. Any field
  left out keeps its live value.
- **`clearStatusBar`** removes any override (`simctl status_bar clear`), restoring the live bar.
- Like `setLocation` / `push`, these need a per-device control channel, so they are
  **unavailable on the fake driver and in parallel runs** and fail cleanly there rather than
  crashing — the same contract already documented for device-control steps.

Prime directives preserved:

- **Determinism.** Each step is a deterministic device mutation with a machine-checkable result;
  no `sleep`-to-settle, no LLM. The run/CI gate stays AI-free.
- **App-agnostic.** No per-app code. Any app-specific value (a status-bar time) lives in the
  scenario, or in `apps.<name>` config when shared — the tool, drivers, and runner are unchanged
  across apps.
- **Codegen.** These have no app-level XCUITest equivalent (they are `simctl`-level), so codegen
  emits a labeled `// TODO` naming the command, consistent with
  [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md).

## Alternatives considered

- **Approximate each effect from inside the app via deeplinks or launch env.** Works per app but
  pushes the burden onto every target app and breaks app-agnosticism — the tool would behave
  differently depending on what hooks an app happens to expose. Rejected as the primary mechanism;
  launch env remains available for genuinely app-specific setup.
- **Drive the system through the alert-guard vision path (screenshot + tap).** That path exists
  for SpringBoard prompts idb can't see, but it is an AI fallback and must never enter the
  deterministic run gate. Rejected — it would put an LLM on the run path.

## References

The remaining device-state primitives (timezone, clipboard seeding, shake, and app resume) are
tracked separately in [Device-state primitives: timezone, clipboard, shake](../../in-progress/BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md).

[DESIGN §6.2](../../../DESIGN.md), `bajutsu/orchestrator/actions/handlers/device.py`
