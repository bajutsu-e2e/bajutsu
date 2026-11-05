**English** · [日本語](BE-0035-device-control-primitives-ja.md)

# BE-0035 — Device-control steps (background, status-bar override)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0035](BE-0035-device-control-primitives.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0035") |
| Implementing PR | [#59](https://github.com/bajutsu-e2e/bajutsu/pull/59) |
| Topic | Scenario authoring features |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

This item adds three deterministic device-control steps that drive the Simulator outside the element tree:

- `background` sends the app to the background.
- `overrideStatusBar` pins the status bar for stable screenshots.
- `clearStatusBar` restores the live status bar.

The item was first scoped to cover more device-state primitives: timezone, clipboard seeding, and shake. Those moved to a separate proposal (see [References](#references)).

## Motivation

Real flows depend on device state that the app does not control. A deterministic side effect on the device is the only way to set that state up reproducibly. The side effect is evaluated outside the element tree, with no AI. Two such needs shipped here:

- **Foreground and background transitions.** Much behavior fires only when the app goes to the background: state restoration, a re-auth prompt, a refresh on resume. `relaunch` restarts the app, which is a different event. Backgrounding the app with the Home button is a case of its own.
- **A deterministic status bar.** Visual-regression assertions ([BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md)) compare screenshots. A live status bar (clock, battery, signal) makes every capture differ. Pinning the status bar to fixed values removes that source of flakiness, and clearing it afterward restores the live bar.

## Detailed design

Each step drives the Simulator through the existing `simctl` / device-control channel. The steps follow the pattern that `setLocation` and `push` already established: a deterministic side effect, evaluated outside the element tree, with no AI.

```yaml
- background: {}                                    # send the app to the background (Home button)
- overrideStatusBar: { time: "9:41", batteryLevel: 100, cellularBars: 4, wifiBars: 3 }
- clearStatusBar: {}                                # restore the live status bar
```

Mapping to the backend:

- **`background`** presses Home to send the app to the background (`simctl ui home`, through the injected device control's `home()`). It takes no fields, because it is the backgrounding action alone. Resuming the app, and a bounded backgrounded interval, are future work in the split-out proposal.
- **`overrideStatusBar`** overrides only the fields the step provides: `time`, `batteryLevel`, `batteryState`, `cellularBars`, and `wifiBars`. It maps to `simctl status_bar override`. Any field left out keeps its live value.
- **`clearStatusBar`** removes any override (`simctl status_bar clear`) and restores the live bar.
- Like `setLocation` / `push`, these steps need a per-device control channel, so they are **unavailable on the fake driver and in parallel runs**. They fail cleanly there instead of crashing, which is the contract already documented for device-control steps.

Prime directives preserved:

- **Determinism.** Each step is a deterministic device mutation with a machine-checkable result. There is no `sleep`-to-settle and no LLM, so the run/CI gate stays AI-free.
- **App-agnostic.** There is no per-app code. An app-specific value (a status-bar time) lives in the scenario, or in `apps.<name>` config when shared. The tool, drivers, and runner are unchanged across apps.
- **Codegen.** These steps have no app-level XCUITest equivalent (they are `simctl`-level), so codegen emits a labeled `// TODO` that names the command, consistent with [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md).

## Alternatives considered

- **Approximate each effect from inside the app through deeplinks or launch env.** This works per app, but it pushes the burden onto every target app and breaks app-agnosticism. The tool would behave differently depending on which hooks an app happens to expose. Rejected as the primary mechanism. Launch env remains available for genuinely app-specific setup.
- **Drive the system through the alert-guard vision path (screenshot plus tap).** That path exists for SpringBoard prompts that idb cannot see, but it is an AI fallback and must never enter the deterministic run gate. Rejected, because it would put an LLM on the run path.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

The remaining device-state primitives (timezone, clipboard seeding, shake, and app resume) are tracked separately in [Device-state primitives: timezone, clipboard, shake](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md).

[DESIGN §6.2](../../DESIGN.md), `bajutsu/orchestrator/actions/handlers/device.py`
