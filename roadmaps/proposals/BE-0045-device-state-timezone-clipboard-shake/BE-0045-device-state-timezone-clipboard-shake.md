**English** · [日本語](BE-0045-device-state-timezone-clipboard-shake-ja.md)

# BE-0045 — Device-state primitives: timezone, clipboard, shake

* Proposal: [BE-0045](BE-0045-device-state-timezone-clipboard-shake.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: MagicPod

## Introduction

The remaining device-state primitives carved out of [BE-0035](../../implemented/BE-0035-device-control-primitives/BE-0035-device-control-primitives.md)
once its first slice shipped (`background`, `overrideStatusBar`, `clearStatusBar`): pinning the
timezone, seeding and reading the clipboard, the shake gesture, and resuming a backgrounded app.

## Motivation

Real flows depend on device state the app does not control, and a test that cannot set that
state cannot exercise the flow deterministically. The gaps that remain after BE-0035:

- **Timezone.** Date-dependent UI (a "Today" header, a countdown, a scheduling screen) can only
  be tested across timezones if the test can pin the device's zone, otherwise the result drifts
  with where CI runs.
- **Clipboard.** Paste flows (a coupon code, a shared link, a one-time code copied from
  elsewhere) need the test to seed the pasteboard and to read it back to verify a "copy" action.
  BE-0035 shipped `clearClipboard` (clearing only); seeding and reading remain.
- **Shake.** Some apps bind "undo" or a debug menu to the shake gesture; there is no way to
  trigger it today.
- **App resume.** BE-0035's `background` sends the app to the background; resuming it (and a
  bounded backgrounded interval) is the other half of a foreground/background transition and is
  not yet implemented.

Without these, such flows fall back to non-deterministic workarounds or simply can't be
automated.

**Competitive context (Maestro).** Closing these is also table-stakes against Maestro, which ships
a broad device-control vocabulary out of the box — `setAirplaneMode` / `toggleAirplaneMode`,
`setOrientation`, `setLocation` / `travel`, `setPermissions`, `clearKeychain`, `clearState`,
`pressKey`, `hideKeyboard`, `openLink`. Bajutsu already shipped the core set in BE-0035; these
remaining primitives (timezone, clipboard seed/read, shake, app resume) remove an easy "Maestro
can, Bajutsu can't" objection. The differentiator is *how*: each stays a deterministic
`simctl`-level side effect with no settle-sleep and no AI, so Bajutsu reaches parity on capability
without giving up the determinism contract.

## Detailed design

Each primitive is a new step that drives the Simulator through the existing `simctl` /
device-control channel, following the pattern `setLocation` / `push` / `background` already
established: a deterministic side effect on the device, no AI, evaluated outside the element tree.
Proposed surface (final names settle on adoption):

```yaml
- setTimezone: { id: "Asia/Tokyo" }          # simctl-level timezone override
- setClipboard: { text: "COUPON123" }        # seed the pasteboard
- shake: {}                                   # the shake gesture
- foreground: {}                              # resume an app sent to the background
```

Mapping to the backend:

- `setTimezone`, `setClipboard`, and `shake` correspond to `simctl` subcommands
  (`simctl status_bar`/`simctl spawn` for the zone, `simctl pbcopy`/`pbpaste`, and the
  shake/device event), built as pure command functions like the existing `boot` / `launch` /
  `openurl` builders, executed through the injectable `RunFn`.
- `foreground` resumes an app that `background` (BE-0035) suspended. Crucially it must **not**
  reintroduce a fixed sleep: any settling after resume is gated on a condition wait (an element
  appearing/disappearing) where the scenario provides one; a bare duration is allowed only as an
  explicit, bounded backgrounded interval, never as a "wait for the app to settle" sleep.
- Like the existing device-control steps, these need a per-device control channel, so they are
  **unavailable on the fake driver and in parallel runs** and fail cleanly there rather than
  crashing.

Prime directives preserved:

- **Determinism.** Each primitive is a deterministic device mutation with a machine-checkable
  result; no `sleep`-to-settle, no LLM. The run/CI gate stays AI-free.
- **App-agnostic.** No per-app code. Where a value is app-specific (a timezone id, a clipboard
  string), it lives in the scenario or, when shared across scenarios, in `apps.<name>` config.
- **Codegen.** These have no app-level XCUITest equivalent (they are `simctl`-level), so codegen
  emits a labeled `// TODO` naming the command, consistent with
  [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md).

## Alternatives considered

- **Approximate each effect from inside the app via deeplinks or launch env.** For example, a
  launch flag that fixes the timezone, or a debug deeplink that simulates a shake. This works
  per app but pushes the burden onto every target app and breaks app-agnosticism. Rejected as the
  primary mechanism; launch env remains available for genuinely app-specific setup.
- **Drive the system through the alert-guard vision path (screenshot + tap).** That path exists
  for SpringBoard prompts idb can't see, but it is an AI fallback and must never enter the
  deterministic run gate. Rejected — it would put an LLM on the run path.
- **Use a fixed `sleep` for the backgrounded interval and for resume settling.** Fixed sleeps are
  banned by design; the bounded backgrounded duration is the one allowed interval, and everything
  after resume must use condition waits. Rejected for resume settling.

## References

Split out of [BE-0035 — Device-control steps](../../implemented/BE-0035-device-control-primitives/BE-0035-device-control-primitives.md).
[DESIGN §6.2](../../../DESIGN.md), `bajutsu/orchestrator/actions/handlers/device.py`
