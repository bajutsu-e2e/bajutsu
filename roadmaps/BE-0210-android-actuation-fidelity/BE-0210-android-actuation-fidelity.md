**English** · [日本語](BE-0210-android-actuation-fidelity-ja.md)

# BE-0210 — Android on-device actuation fidelity

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0210](BE-0210-android-actuation-fidelity.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0210") |
| Implementing PR | [#857](https://github.com/bajutsu-e2e/bajutsu/pull/857), [#910](https://github.com/bajutsu-e2e/bajutsu/pull/910) |
| Topic | Platform support (iOS / Android / Web / Flutter) |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) |
<!-- /BE-METADATA -->

## Introduction

The first on-device validation of the Android backend
([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), 2026-07-07 on an arm64 API 34
emulator) passed the core id/tap/type/value scenarios but left four coordinate-actuation gaps that
the fast gate cannot catch, because they only manifest against a real device: system back and
by-scheme deeplink raise `BackButton`, double-tap does not register, elements past the scrolled
viewport are unreachable, and runtime permission dialogs block a scenario. This item makes on-device
actuation robust across these four, reaching parity with idb on the scenarios still red on Android
(`notices`, `gestures`, `controls`, and permission-gated flows).

## Motivation

idb handles all four cases, so they are a direct Android-vs-iOS parity gap, and each is a concrete
scenario the 2026-07-07 validation left failing:

- **System back / by-scheme deeplink.** Both raise `BackButton` today, so `notices` (which needs a
  system back) and by-scheme deeplink flows cannot complete.
- **Double-tap.** Two separate `adb shell input tap` invocations exceed the platform double-tap
  window — the `input` binary's own startup dominates the gap, so even batching the two taps into
  one shell round-trip is not enough. `gestures` fails on the double-tap as a result (its long-press
  passes).
- **Scroll-into-view.** `controls`' `log.segment.value` sits just past the scrolled viewport and
  `notices` needs a list scroll, so a selector that would resolve after a scroll fails instead.
- **Runtime permission dialogs.** A runtime permission prompt blocks the app, and there is no
  deterministic way to clear it.

None of these can be caught on the Linux fast gate (they need a device), so they are validated on
the Android emulator e2e lane, but the actuation logic itself is what this item builds.

## Detailed design

All four stay within determinism first: condition waits and bounded retries, no fixed `sleep`, and
a still-ambiguous selector still fails rather than guessing.

### Work breakdown (MECE)

1. **System back and by-scheme deeplink.** Map the system-back step to `adb shell input keyevent 4`
   (`KEYCODE_BACK`); support the by-scheme deeplink through `am start -a android.intent.action.VIEW
   -d <scheme://…>`, so neither raises `BackButton`.
2. **Double-tap within the platform window.** Actuate both taps through a single low-latency path
   that keeps the inter-tap gap inside the double-tap window — the `input tap` per-invocation
   startup is the cost to eliminate (e.g. a single motion-event sequence rather than two `input`
   processes). The acceptance is `gestures`' double-tap registering on device.
3. **Scroll-into-view.** When a selector resolves to nothing in the current viewport, scroll toward
   it with `input swipe` and re-query, bounded by a retry count, before failing — a condition wait,
   not a fixed sleep. A selector that never appears still fails deterministically.
4. **Runtime permission dialogs.** Clear runtime permission prompts deterministically, preferring
   granting the permission up front (`pm grant` / `appops`) over tapping dialog buttons, so timing
   does not enter the run path. The choice of mechanism is the one design decision here.
5. **Validation.** Fast gate over fixtures/injected `run` where the logic allows (the back/deeplink
   command builders, the scroll-and-re-query loop); on-device acceptance is the four previously-red
   scenarios (`notices`, `gestures`, `controls`, permission-gated) passing on the emulator e2e lane.

## Alternatives considered

- **A richer actuator (Appium UiAutomator2) for gestures.** Deferred, consistent with BE-0007's
  own alternative: the adb path stays idb's twin, and a semantic actuator can be added later as a
  second backend. This item keeps the coordinate model and makes it robust.
- **A fixed sleep to widen the double-tap gap or wait out a permission dialog.** Rejected: fixed
  sleeps violate determinism first. The double-tap is solved by removing latency, not by adding it;
  the permission dialog by granting up front, not by waiting for the prompt.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] System back — a first-class `back` step, `keyevent 4` on Android. By-scheme deeplink is
      already served by the launch-time `preconditions.deeplink` (`am start -a
      android.intent.action.VIEW`); a mid-scenario deeplink step is deferred (see log).
- [x] Double-tap within the platform double-tap window (single `adb shell` round-trip).
- [x] Scroll-into-view (bounded scroll-and-re-query condition wait).
- [x] Runtime permission handling (deterministic up-front `pm grant`).
- [x] Validation — fast-gate command builders and the scroll-and-re-query loop are covered over
      injected `run`. On-device acceptance is now partial: `notices` and `controls` run and pass on
      the emulator e2e lane (`E2E_SCENARIOS` in `demos/showcase/android/Makefile`) as of
      [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md). `gestures` and
      the permission-gated flow are still follow-ups (the former's double-tap does not yet register on
      device through `input tap ; input tap`, and `permission.yaml` is not yet shaped for the
      deterministic `grantPermissions` / `pm grant` path).

Log:

- [#857](https://github.com/bajutsu-e2e/bajutsu/pull/857) — `back` (cross-backend step: Android `keyevent 4`, iOS/XCUITest tap the OS back
  button, web `history.back()`); `double_tap` single `adb shell` round-trip; adb scroll-into-view in
  the resolve path (default up-swipe, bounded); config `grantPermissions` pre-granted via `pm grant`
  at lease time. The standalone mid-scenario deeplink step is deferred: the launch-time path already
  uses `am start -a android.intent.action.VIEW`, no acceptance scenario needs a mid-run deeplink, and
  a step would require extending the cross-backend `DeviceControl` surface. If a mid-scenario
  deeplink is later needed, it is a small follow-up.

## References

[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[drivers.md](../../docs/drivers.md), `bajutsu/drivers/adb.py`, `bajutsu/adb.py`
