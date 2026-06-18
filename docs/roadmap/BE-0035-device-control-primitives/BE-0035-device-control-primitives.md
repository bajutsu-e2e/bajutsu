**English** · [日本語](BE-0035-device-control-primitives-ja.md)

# BE-0035 — Extended device-control primitives

* Proposal: [BE-0035](BE-0035-device-control-primitives.md)
* Status: **Proposal**
* Track: [Proposals](../README.md#proposals)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: MagicPod

## Introduction

Location (`setLocation`) and push notifications (`push`) are implemented. Timezone, clipboard, foreground-background transitions, and shake remain unimplemented (`rotate`/`swipe`/`pinch` already exist).

## Motivation

Real flows depend on device state the app does not control, and a test that cannot set that
state cannot exercise the flow deterministically. Concrete gaps competitive research surfaced:

- **Timezone.** Date-dependent UI (a "Today" header, a countdown, a scheduling screen) can only
  be tested across timezones if the test can pin the device's zone, otherwise the result drifts
  with where CI runs.
- **Clipboard.** Paste flows (a coupon code, a shared link, a one-time code copied from
  elsewhere) need the test to seed the pasteboard and to read it back to verify a "copy" action.
- **Foreground/background transitions.** A lot of behavior fires only on backgrounding or
  re-foregrounding (state restoration, a re-auth prompt, a refresh-on-resume). `relaunch`
  restarts the app, which is a different event; suspend/resume is its own case.
- **Shake.** Some apps bind "undo" or a debug menu to the shake gesture; there is no way to
  trigger it today.

Without these, such flows fall back to non-deterministic workarounds or simply can't be
automated.

## Detailed design

Each primitive is a new step that drives the simulator through the existing `simctl` /
device-control channel, following the pattern `setLocation` and `push` already established: a
deterministic side effect on the device, no AI, evaluated outside the element tree. Proposed
surface (final names settle on adoption):

```yaml
- setTimezone: { id: "Asia/Tokyo" }          # simctl-level timezone override
- setClipboard: { text: "COUPON123" }        # seed the pasteboard
- background: { seconds: 3 }                  # suspend, then resume after a condition/duration
- shake: {}                                   # the shake gesture
```

Mapping to the backend:

- `setTimezone`, `setClipboard`, and `shake` correspond to `simctl` subcommands
  (`simctl status_bar`/`simctl spawn` for the zone, `simctl pbcopy`/`pbpaste`, and the
  shake/device event), built as pure command functions in `env.py` exactly like the existing
  `boot` / `launch` / `openurl` builders, executed through the injectable `RunFn`.
- `background` suspends and resumes the app. Crucially it must **not** reintroduce a fixed
  sleep: resume is gated on a condition wait (an element appearing/disappearing) where the
  scenario provides one; a bare duration is allowed only as the explicit, bounded backgrounded
  interval, never as a "wait for the app to settle" sleep. The directive that the only wait is a
  condition wait still holds for everything that follows the resume.
- Like `setLocation` / `push`, these need a per-device control channel, so they are
  **unavailable on the fake driver and in parallel runs** and fail cleanly there rather than
  crashing — the same contract already documented for device-control steps.

Prime directives preserved:

- **Determinism.** Each primitive is a deterministic device mutation with a machine-checkable
  result; no `sleep`-to-settle, no LLM. The run/CI gate stays AI-free.
- **App-agnostic.** No per-app code. Where a value is app-specific (a timezone id, a clipboard
  string), it lives in the scenario or, when shared across scenarios, in `apps.<name>` config —
  the tool, drivers, and runner are unchanged across apps.
- **Codegen.** These have no app-level XCUITest equivalent (they are `simctl`-level), so codegen
  emits a labeled `// TODO` naming the command, consistent with
  [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md).

## Alternatives considered

- **Approximate each effect from inside the app via deeplinks or launch env.** For example, a
  launch flag that fixes the timezone, or a debug deeplink that simulates a shake. This works
  per app but pushes the burden onto every target app and breaks app-agnosticism — the tool
  would behave differently depending on what hooks an app happens to expose. Rejected as the
  primary mechanism; launch env remains available for genuinely app-specific setup.
- **Drive the system through the alert-guard vision path (screenshot + tap).** That path exists
  for SpringBoard prompts idb can't see, but it is an AI fallback and must never enter the
  deterministic run gate. Using it for routine device control would put an LLM on the run path.
  Rejected — it violates the prime directive directly.
- **Use a fixed `sleep` for the backgrounded interval and for resume settling.** Simpler, but
  fixed sleeps are banned by design; the bounded backgrounded duration is the one allowed
  interval, and everything after resume must use condition waits. Rejected for resume settling.

## References

[DESIGN §6.2](../../../DESIGN.md), `bajutsu/scenario/`
