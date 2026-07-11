**English** · [日本語](BE-XXXX-adb-clipboard-fidelity-ja.md)

# BE-XXXX — adb clipboard on-device fidelity

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-adb-clipboard-fidelity.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md) added the
emulator-backed subset of the device-control family to the adb backend: `setLocation` (over
`emu geo fix`) and the clipboard operations (`setClipboard` / `getClipboard` / `clearClipboard`,
built on `cmd clipboard set/get/clear-primary-clip`). The backend advertises both
`DC_SET_LOCATION` and `DC_CLIPBOARD` as capabilities, so the per-operation preflight (BE-0212)
admits clipboard steps and the clipboard read-back assertion.

The clipboard half does not work on a real device. On the google_apis API 34 emulator image,
`cmd clipboard set/get-primary-clip` returns `No shell command implementation` — the `clipboard`
system service exists and is listed by `cmd -l`, but it does not implement the shell-command
interface those builders drive. A `setClipboard` therefore silently seeds nothing and a
`clipboard` read-back returns empty. This item makes the advertised `DC_CLIPBOARD` capability
honest: either drive the clipboard through a mechanism that works on-device, or narrow the
capability so preflight rejects clipboard steps rather than letting them fail at assertion time.

## Motivation

A capability the backend advertises but cannot honor is worse than an unadvertised one: preflight
(BE-0212) exists precisely so that a scenario using an unsupported step fails *fast, before the
run*, with a clear reason — rather than deep in the run at an assertion that can't be satisfied.
`DC_CLIPBOARD` on adb defeats that contract. A scenario that seeds the clipboard and asserts the
read-back passes preflight, runs, and then fails at the assertion with `clipboard was ''`, which
reads like an app bug rather than a backend limitation.

The gap went unnoticed because BE-0211's clipboard round-trip was only ever exercised against a
**fake injected `run`** (`test_android_device_control.py`'s `fake_run` stores and echoes the text
in a dict), never against a device. The fake proves the command *builders* and the delegation
wiring, but not that the device honors the commands. It was discovered on-device while shipping
BE-0208's device-control lane slice ([PR #934](https://github.com/bajutsu-e2e/bajutsu/pull/934)),
which had to drop the clipboard half and ship `setLocation` alone.

This matters beyond the one emulator image. The device-control family is a cross-backend surface
(idb honors the full set via `simctl`; adb the emulator subset), and an assertion kind
(`clipboard`) depends on the read-back working. Leaving `DC_CLIPBOARD` advertised-but-broken
erodes trust in the capability model that keeps the tool app-agnostic (prime directive 3).

## Detailed design

### Work breakdown (MECE)

1. **Establish the on-device baseline.** Determine, across the API levels the project targets
   (at least the CI lane's x86_64 API 34 and the local arm64 API 34), which clipboard mechanisms a
   shell-uid process can actually drive: `cmd clipboard` subcommands, `service call clipboard` with
   raw parcels, or a broadcast to a small in-app receiver. Record what works where, so the decision
   in unit 2 rests on evidence, not on one image's behavior.

2. **Decide: repair or narrow.** Based on unit 1, choose one:
   - *Repair* — replace the `cmd clipboard` builders with a mechanism that works on-device, keeping
     the `DeviceControl` interface (`set_clipboard` / `get_clipboard` / `clear_clipboard`) and the
     `DC_CLIPBOARD` capability unchanged so scenarios are untouched.
   - *Narrow* — drop `DC_CLIPBOARD` from the adb backend's advertised capabilities so preflight
     rejects clipboard steps with a clear reason, and make the `DeviceControl` clipboard methods
     raise `UnsupportedAction` on adb (the runtime backstop the other unsupported operations already
     use). idb keeps the capability.

   The decision is recorded in this item and in `DESIGN.md` / `docs/architecture.md` if the
   capability surface changes (BE-0113).

3. **Implement the chosen direction** in `bajutsu/adb.py`, `bajutsu/platform_lifecycle.py`, and
   `bajutsu/drivers/adb.py`, keeping the change within the adb backend (no cross-backend churn).

4. **Test against a device, not only a fake.** Add coverage that would have caught this: a test
   that runs the real clipboard path on a booted emulator (guarded so it stays off the fast Linux
   gate, like the other on-device checks), or — if unit 2 narrows — a fast-gate test asserting
   preflight now rejects clipboard steps on adb and the methods raise. The fake-runner tests stay,
   but they no longer stand in for on-device proof.

5. **Reconcile the e2e lane.** If unit 2 *repairs* the clipboard, extend BE-0208's `device_android`
   scenario to seed and read back the clipboard (the strong assertion PR #934 wanted), and note the
   restored coverage on BE-0208. If unit 2 *narrows*, leave `device_android` at `setLocation` only
   and record that the clipboard step is unsupported on adb by design.

## Alternatives considered

- **Leave it as-is (advertised but broken).** Rejected: it violates the preflight contract and
  reads as an app bug on-device. The whole point of the capability model is that the tool fails
  fast and honestly per backend.
- **Fix it inside BE-0208's lane PR.** Rejected: BE-0208 is about the CI e2e lane, not the adb
  device-control implementation. Touching the driver there would cross lanes (the change belongs to
  the BE-0211 surface), so the lane PR shipped `setLocation` only and deferred the clipboard to this
  item.
- **Root-only clipboard via `service call clipboard`.** A candidate mechanism for unit 1/2, not a
  decision here: raw parcel calls are brittle across API levels and encodings, so they need the
  unit-1 baseline before being adopted over the narrow option.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — establish the on-device clipboard baseline across the targeted API levels / ABIs.
- [ ] Unit 2 — decide repair vs. narrow, and record the decision (+ DESIGN/architecture if the
  capability surface changes).
- [ ] Unit 3 — implement the chosen direction within the adb backend.
- [ ] Unit 4 — add on-device (or preflight-rejection) coverage that would have caught the gap.
- [ ] Unit 5 — reconcile BE-0208's `device_android` lane scenario with the outcome.

## References

[BE-0211 — Android device control](../BE-0211-android-device-control/BE-0211-android-device-control.md),
[BE-0208 — Android on-device e2e in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
[PR #934](https://github.com/bajutsu-e2e/bajutsu/pull/934),
`bajutsu/adb.py`, `bajutsu/platform_lifecycle.py`, `bajutsu/drivers/adb.py`,
`bajutsu/capability_preflight.py`
