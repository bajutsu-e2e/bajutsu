**English** · [日本語](BE-0052-device-state-timezone-clipboard-shake-ja.md)

# BE-0052 — Device-state primitives: timezone, clipboard, shake

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0052](BE-0052-device-state-timezone-clipboard-shake.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0052") |
| Implementing PR | [#206](https://github.com/bajutsu-e2e/bajutsu/pull/206), [#257](https://github.com/bajutsu-e2e/bajutsu/pull/257), [#378](https://github.com/bajutsu-e2e/bajutsu/pull/378) |
| Topic | Scenario authoring features |
| Related | [BE-0157](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive.md), [BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

[BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md) shipped its first slice (`background`, `overrideStatusBar`, `clearStatusBar`). This item carries the device-state primitives that remained. They pin the timezone, seed and read the clipboard, perform the shake gesture, and resume a backgrounded app.

## Motivation

Real flows depend on device state that the app does not control. A test that cannot set that state cannot exercise the flow deterministically. BE-0035 left four gaps:

- **Timezone.** Date-dependent UI (a "Today" header, a countdown, a scheduling screen) can be tested across timezones only if the test can pin the device's zone. Without the pin, the result drifts with where CI runs.
- **Clipboard.** Paste flows (a coupon code, a shared link, a one-time code copied from elsewhere) need the test to seed the pasteboard. A "copy" action needs the test to read the pasteboard back for verification. BE-0035 shipped `clearClipboard`, which clears the pasteboard and nothing more. Seeding and reading remain.
- **Shake.** Some apps bind "undo" or a debug menu to the shake gesture. No step triggers that gesture today.
- **App resume.** BE-0035's `background` sends the app to the background. Resuming the app, and holding it backgrounded for a bounded interval, is the other half of a foreground/background transition. Neither is implemented.

Without these primitives, such flows fall back to non-deterministic workarounds or cannot be automated at all.

**Competitive context (Maestro).** Maestro ships a broad device-control vocabulary out of the box, including `setAirplaneMode` / `toggleAirplaneMode`, `setOrientation`, `setLocation` / `travel`, `setPermissions`, `clearKeychain`, `clearState`, `pressKey`, `hideKeyboard`, and `openLink`. Matching it is table stakes. Bajutsu already shipped the core set in BE-0035. The remaining primitives (timezone, clipboard seed and read, shake, app resume) remove an easy "Maestro can, Bajutsu can't" objection.

The differentiator is *how*. Each primitive stays a deterministic `simctl`-level side effect with no settle-sleep and no AI. Bajutsu reaches parity on capability without giving up the determinism contract.

## Detailed design

Each primitive is a new step. It drives the Simulator through the existing `simctl` and device-control channel and follows the pattern that `setLocation`, `push`, and `background` established. That pattern is a deterministic side effect on the device, with no AI, evaluated outside the element tree. The proposed surface follows. Final names settle on adoption.

```yaml
- setTimezone: { id: "Asia/Tokyo" }          # simctl-level timezone override
- setClipboard: { text: "COUPON123" }        # seed the pasteboard
- shake: {}                                   # the shake gesture
- foreground: {}                              # resume an app sent to the background
```

Mapping to the backend:

- `setTimezone`, `setClipboard`, and `shake` correspond to `simctl` subcommands: `simctl status_bar` or `simctl spawn` for the zone, `simctl pbcopy` and `pbpaste` for the clipboard, and the shake device event. Each is built as a pure command function, like the existing `boot`, `launch`, and `openurl` builders. The injectable `RunFn` executes it.
- `foreground` resumes an app that `background` (BE-0035) suspended. It must **not** reintroduce a fixed sleep. A condition wait gates any settling after the resume, where the scenario provides one: an element appearing or disappearing. A bare duration is allowed only as an explicit, bounded backgrounded interval. It is never a "wait for the app to settle" sleep.
- Like the existing device-control steps, these steps need a per-device control channel. They are **unavailable on the fake driver and in parallel runs**, and they fail cleanly there instead of crashing.

The primitives preserve the prime directives:

- **Determinism.** Each primitive is a deterministic device mutation with a machine-checkable result. It uses no `sleep` to settle and no large language model (LLM). The run/CI gate stays AI-free.
- **App-agnostic.** No per-app code exists. An app-specific value (a timezone id, a clipboard string) lives in the scenario. When several scenarios share it, it lives in the `apps.<name>` config.
- **Codegen.** These steps have no app-level XCUITest equivalent, because they work at the `simctl` level. Codegen emits a labeled `// TODO` that names the command, consistent with [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md).

## Alternatives considered

- **Approximate each effect from inside the app through deeplinks or launch env.** Examples are a launch flag that fixes the timezone and a debug deeplink that simulates a shake. This works per app, but it pushes the burden onto every target app and breaks app-agnosticism. Rejected as the primary mechanism. Launch env remains available for genuinely app-specific setup.
- **Drive the system through the alert-guard vision path (screenshot plus tap).** That path exists for SpringBoard prompts that idb cannot see. It is an AI fallback and must never enter the deterministic run gate. Rejected, because it would put an LLM on the run path.
- **Use a fixed `sleep` for the backgrounded interval and for resume settling.** Fixed sleeps are banned by design. The bounded backgrounded duration is the one allowed interval, and everything after the resume must use condition waits. Rejected for resume settling.

## Progress

- [x] `setClipboard` and `foreground` (resume a backgrounded app) — the first slice ([#206](https://github.com/bajutsu-e2e/bajutsu/pull/206)).
- [x] Clipboard read-back assertion — `expect: - clipboard: { equals | matches }` via `simctl pbpaste` ([#257](https://github.com/bajutsu-e2e/bajutsu/pull/257)).
- [x] `setTimezone` — implementation triage found no reliable `simctl` actuation exists today (shipping a command that does not take effect would violate determinism-first): neither `simctl` nor `idb` has a `timezone` subcommand, and the Simulator's timezone is inherited from the host Mac rather than settable per device. A launch-time `SIMCTL_CHILD_TZ` only moves the app process's libc `localtime`, not the `TimeZone.current` most iOS date UI reads, so it would appear to succeed without changing the UI under test. Changing the host Mac's own timezone would work but is global, `sudo`-gated, and mutates the developer/CI clock across every booted Simulator — out of contract for a per-device primitive. Carved out into its own deferred item, [BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive.md), to wait on a verified mechanism.
- [x] `shake` — implementation triage found there is no `simctl` / `idb` shake command (it is a Hardware-menu gesture). It can be triggered deterministically via GUI automation (AppleScript driving the Simulator's Device ▸ Shake menu item, or a third-party tool such as RocketSim), but that path needs the Simulator GUI running plus an Accessibility grant for the controlling process, is scoped to the focused Simulator, and cannot run in headless CI — so it does not fit as a `simctl`-level primitive today. RocketSim's own CLI does not expose shake (or timezone) either; only its `tap`/`swipe`/`type`/`button`/inspect commands are scriptable. Carved out into its own deferred item, [BE-0157](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive.md), to wait on a verified mechanism.

The work shipped across [#206](https://github.com/bajutsu-e2e/bajutsu/pull/206), [#257](https://github.com/bajutsu-e2e/bajutsu/pull/257), and [#378](https://github.com/bajutsu-e2e/bajutsu/pull/378). `setTimezone` and `shake` had no reliable actuator. They moved to their own deferred items, [BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive.md) and [BE-0157](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive.md). This item is complete for the primitives it could ship.

## References

Split out of [BE-0035 — Device-control steps](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md). `setTimezone` and `shake` were in turn split out into [BE-0158 — Timezone device primitive](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive.md) and [BE-0157 — Shake device primitive](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive.md). See also [DESIGN §6.2](../../DESIGN.md) and `bajutsu/orchestrator/actions/handlers/device.py`.
