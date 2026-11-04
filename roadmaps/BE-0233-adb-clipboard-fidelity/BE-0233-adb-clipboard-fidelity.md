**English** · [日本語](BE-0233-adb-clipboard-fidelity-ja.md)

# BE-0233 — adb clipboard on-device fidelity

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0233](BE-0233-adb-clipboard-fidelity.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0233") |
| Implementing PR | [#949](https://github.com/bajutsu-e2e/bajutsu/pull/949) |
| Topic | Platform support |
| Related | [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md) added the
emulator-backed subset of the device-control family to the adb backend. The subset has two parts:

- `setLocation`, over `emu geo fix`;
- the clipboard operations (`setClipboard` / `getClipboard` / `clearClipboard`), built on
  `cmd clipboard set/get/clear-primary-clip`.

The backend advertises both `DC_SET_LOCATION` and `DC_CLIPBOARD` as capabilities. The per-operation
preflight (BE-0212) therefore admits clipboard steps and the clipboard read-back assertion.

The clipboard half does not work on a real device. On the google_apis API 34 emulator image,
`cmd clipboard set/get-primary-clip` returns `No shell command implementation`. The `clipboard`
system service exists and `cmd -l` lists it, but it does not implement the shell-command interface
that those builders drive. A `setClipboard` therefore silently seeds nothing, and a `clipboard`
read-back returns empty.

This item makes the advertised `DC_CLIPBOARD` capability honest. There are two ways to do so:

- drive the clipboard through a mechanism that works on-device;
- narrow the capability, so preflight rejects clipboard steps instead of letting them fail at
  assertion time.

## Motivation

A capability that the backend advertises but cannot honor is worse than an unadvertised one. Preflight
(BE-0212) exists so that a scenario using an unsupported step fails *fast, before the run*, with a
clear reason. Without it, the scenario fails deep in the run, at an assertion that cannot be
satisfied. `DC_CLIPBOARD` on adb defeats that contract.

A scenario that seeds the clipboard and asserts the read-back passes preflight and runs. It then
fails at the assertion with `clipboard was ''`. That reads like an app bug rather than a backend
limitation.

The gap went unnoticed because BE-0211's clipboard round-trip was only ever exercised against a
**fake injected `run`**. In `test_android_device_control.py`, `fake_run` stores and echoes the text in
a dict. It never touched a device. The fake proves the command *builders* and the delegation wiring.
It does not prove that the device honors the commands.

The gap was discovered on-device while shipping BE-0208's device-control lane slice
([PR #934](https://github.com/bajutsu-e2e/bajutsu/pull/934)). That PR had to drop the clipboard half
and ship `setLocation` alone.

The problem reaches beyond the one emulator image. The device-control family is a cross-backend
surface: idb honors the full set via `simctl`, and adb honors the emulator subset. An assertion kind
(`clipboard`) depends on the read-back working. Leaving `DC_CLIPBOARD` advertised-but-broken erodes
trust in the capability model that keeps the tool backend-agnostic.

That model rests on the "platform is a backend" premise in `CLAUDE.md`. The premise is distinct from
prime directive 3, which covers per-app agnosticism, not per-backend.

## Detailed design

### Work breakdown (MECE)

1. **Establish the on-device baseline.** Determine which clipboard mechanisms a shell-uid process can
   actually drive. Cover the API levels the project targets: at least the CI lane's x86_64 API 34 and
   the local arm64 API 34. The candidate mechanisms are:
   - `cmd clipboard` subcommands;
   - `service call clipboard` with raw parcels;
   - a broadcast to a small in-app receiver.

   Record what works where. The decision in unit 2 then rests on evidence, not on one image's
   behavior.

2. **Decide: repair or narrow.** Based on unit 1, choose one:
   - *Repair* — replace the `cmd clipboard` builders with a mechanism that works on-device. Keep the
     `DeviceControl` interface (`set_clipboard` / `get_clipboard` / `clear_clipboard`) and the
     `DC_CLIPBOARD` capability unchanged, so scenarios are untouched.
   - *Narrow* — drop `DC_CLIPBOARD` from the adb backend's advertised capabilities, so preflight
     rejects clipboard steps with a clear reason. Make the `DeviceControl` clipboard methods raise
     `UnsupportedAction` on adb. That is the runtime backstop the other unsupported operations
     already use. idb keeps the capability.

   Record the decision in this item. Record it in `DESIGN.md` and `docs/architecture.md` too, if the
   capability surface changes (BE-0113).

3. **Implement the chosen direction** in `bajutsu/adb.py`, `bajutsu/platform_lifecycle.py`, and
   `bajutsu/drivers/adb.py`. Keep the change within the adb backend, with no cross-backend churn.

4. **Test against a device, not only a fake.** Add coverage that would have caught this gap. One
   option is a test that runs the real clipboard path on a booted emulator. Guard it so it stays off
   the fast Linux gate, like the other on-device checks. If unit 2 narrows, the alternative is a
   fast-gate test. It asserts that preflight now rejects clipboard steps on adb and that the methods
   raise. The fake-runner tests stay, but they no longer stand in for on-device proof.

5. **Reconcile the e2e lane.** The action depends on the outcome of unit 2:
   - If unit 2 *repairs* the clipboard, extend BE-0208's `device_android` scenario to seed and read
     back the clipboard. That is the strong assertion PR #934 wanted. Note the restored coverage on
     BE-0208.
   - If unit 2 *narrows*, leave `device_android` at `setLocation` only. Record that the clipboard
     step is unsupported on adb by design.

## Alternatives considered

- **Leave it as-is (advertised but broken).** Rejected. It violates the preflight contract and reads
  as an app bug on-device. The whole point of the capability model is that the tool fails fast and
  honestly per backend.
- **Fix it inside BE-0208's lane PR.** Rejected. BE-0208 is about the CI e2e lane, not the adb
  device-control implementation. Touching the driver there would cross lanes, because the change
  belongs to the BE-0211 surface. The lane PR therefore shipped `setLocation` only and deferred the
  clipboard to this item.
- **Root-only clipboard via `service call clipboard`.** A candidate mechanism for units 1 and 2, not a
  decision here. Raw parcel calls are brittle across API levels and encodings. They need the unit 1
  baseline before being adopted over the narrow option.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — establish the on-device clipboard baseline across the targeted API levels / ABIs.
- [x] Unit 2 — decide repair vs. narrow, and record the decision (+ DESIGN/architecture if the
  capability surface changes).
- [x] Unit 3 — implement the chosen direction within the adb backend.
- [x] Unit 4 — add on-device (or preflight-rejection) coverage that would have caught the gap.
- [x] Unit 5 — reconcile BE-0208's `device_android` lane scenario with the outcome.

**Log**

- Unit 1 (baseline, arm64 API 34 emulator): `cmd clipboard set/get-primary-clip` returns `No shell
  command implementation` and exits 0 (a silent no-op); `service call clipboard` requires
  hand-marshaled `ClipData` parcels whose transaction codes shift across API levels and routes
  through `ClipboardService.checkAndSetPrimaryClip`, so it is brittle and blocked; a shell-uid process
  cannot reach the clipboard at all (Android 10+ restricts it to the foreground app / default IME).
  The finding is platform-, not ABI-, specific, matching the x86_64 image PR #934 observed.
- Unit 2 (decision): **repair**, not narrow. Since no shell mechanism works, the clipboard is driven
  from inside the app — the app under test is foreground while a scenario runs it. This leverages a
  uniform, opt-in app-side SDK, which stays app-agnostic (every app embeds the same library, not a
  per-app config difference), the same model BajutsuKit uses for network capture on iOS. `DC_CLIPBOARD`
  stays advertised on adb (the backend can drive it given a cooperating app, as idb's clipboard rides
  simctl). The capability-model clarification is recorded in `DESIGN.md` §8 and `docs/architecture.md`
  / `docs/drivers.md` (BE-0113).
- Unit 3 (PR #949): new reusable library `BajutsuAndroid/` (the Android peer of `BajutsuKit/`) with a
  clipboard `BroadcastReceiver`; `bajutsu/adb.py` drives it over an ordered `am broadcast` (base64
  both ways, loud-fail when the app has no receiver), `platform_lifecycle.android_device_control`
  threads the package through, `drivers/adb.py` keeps `DC_CLIPBOARD`.
- Unit 4: fast-gate tests cover the broadcast builders, the result parse (base64 + loud-fail), the
  round-trip against a fake receiver, and the kept capability / preflight admit. On-device: the
  `device_android` clipboard read-back passes on a local arm64 API 34 emulator.
- Unit 5 (PR #949): `device_android.yaml` regained the clipboard seed + read-back (the strong
  assertion PR #934 wanted); the showcase compose/views apps embed the receiver in debug builds.

## References

[BE-0211 — Android device control](../BE-0211-android-device-control/BE-0211-android-device-control.md),
[BE-0208 — Android on-device e2e in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
[PR #934](https://github.com/bajutsu-e2e/bajutsu/pull/934),
`bajutsu/adb.py`, `bajutsu/platform_lifecycle.py`, `bajutsu/drivers/adb.py`,
`bajutsu/capability_preflight.py`
