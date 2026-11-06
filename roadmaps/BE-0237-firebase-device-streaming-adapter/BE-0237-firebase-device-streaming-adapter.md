**English** · [日本語](BE-0237-firebase-device-streaming-adapter-ja.md)

# BE-0237 — Firebase Test Lab / Device Streaming adapter

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0237](BE-0237-firebase-device-streaming-adapter.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0237") |
| Topic | Device-cloud execution |
<!-- /BE-METADATA -->

## Introduction

This item adds the first concrete **device provider** on top of the *device-cloud-provider-
abstraction* seam. It runs a Bajutsu Android scenario against a Google-hosted real device through
**Firebase's Android Device Streaming**. Device Streaming exposes a reserved device as "adb over
SSL".

The Android backend already drives any reachable `adb` serial, including an `IP:port` `adb connect`
target. The adapter's job is therefore narrow:

- Reserve the device.
- Establish the adb-over-SSL connection.
- Hand the runner a serial.
- Release the reservation afterwards.

## Motivation

Firebase is a natural first target. Many teams already use it, and Device Streaming provides exactly
the shape that the live-topology seam wants: a reserved real device reachable over adb. Landing
Firebase first also **validates the provider seam against a real service** before the abstraction is
frozen. This is the PoC-first sequencing that the foundation item calls for.

The investigation produced a key finding. It is recorded here so that the scope is not misread:
**Firebase Test Lab proper cannot host Bajutsu's driver.**

- Test Lab accepts only fixed test types: Instrumentation (Espresso / UI Automator), Robo, and Game
  Loop. They are uploaded as APK/AAB via `gcloud firebase test android run`.
- No path exists to upload an arbitrary Python driver and have it drive the device out-of-band.
- Test Lab is a closed test runner, not an open execution sandbox.

The viable route to a live adb device on Google's cloud is the **separate** Android Device Streaming
product (Android Studio's device reservation). It explicitly grants an "adb over SSL" connection
usable by "any tool that uses adb". This item targets Device Streaming. It documents the Test Lab
limitation, and it does not pretend to wrap Test Lab.

## Detailed design

Implement a `DeviceProvider` (kind `firebase-streaming`) as an **optional extra** (e.g.
`pip install "bajutsu[firebase]"`). The extra wraps the Firebase / Android Device Streaming CLI. The
provider has two operations:

- **acquire(target).** Reserve a device, taking the device model, API level, and project from the
  target's `deviceProvider` config. Establish the adb-over-SSL tunnel. Wait until the streamed
  device shows ready in `adb devices`. Return a `DeviceLease` whose serial is the streamed `IP:port`
  target.
- **release().** Tear down the tunnel and end the reservation, which stops billing.

Everything downstream is unchanged. `make_driver("adb", serial)` drives the streamed device exactly
as it drives a local emulator, because the serial is the only thing the driver needs.

The cloud-difference hooks defined by the foundation item apply here:

- The device is already booted, so the boot-wait is skipped.
- App install runs through the normal `adb install` over the tunnel, because Device Streaming does
  not pre-install.
- The streamed device lacks some emulator-only device-control primitives. The provider declares them
  unsupported, so that preflight cuts them loudly.

### Work breakdown (MECE)

1. **Adapter skeleton.** Register the `firebase-streaming` provider in the seam's registry, and ship
   it behind the `bajutsu[firebase]` extra. The import of the extra is lazy, so the gate stays
   cloud-free.
2. **Reservation + adb-over-SSL.** Reserve via the Device Streaming CLI and establish the tunnel.
   Resolve the streamed serial. Confirm readiness through `adb devices`, as a condition wait with no
   fixed sleep.
3. **Lease lifecycle.** Make `release()` reliable on success, on failure, and on interruption, so
   that a reservation is never leaked (billing safety).
4. **Cloud-difference hooks.** Wire the boot-wait skip, the normal `adb install`, and device-control
   capability degradation through the foundation's `RunEnvironment` hooks.
5. **Config.** The adapter validates the provider-specific fields (project, device model, API
   level). An unknown or missing field raises a loud error.
6. **Tests.** Fake the CLI/tunnel boundary, since an external process or network is the sanctioned
   mock point. Assert acquire→serial→release, readiness as a condition, and lease cleanup on failure.
   No live Firebase enters the gate.
7. **Docs.** Add a Firebase how-to in `docs/` (both languages). It states plainly that Device
   Streaming is the supported path and that Test Lab proper cannot be wrapped.

### Prime-directive compliance

- **AI out of the gate.** The adapter reserves, connects, and releases. It adds no model and has no
  effect on the deterministic verdict.
- **Determinism first.** Readiness is a condition on `adb devices`, not a sleep. Driving the streamed
  device is as reproducible as driving a local one.
- **App-agnostic.** All Firebase specifics live in the target's `deviceProvider` config and the
  optional adapter. The driver and runner are untouched.

## Alternatives considered

- **Wrap Firebase Test Lab proper (Instrumentation/Robo/Game Loop).** Rejected, and documented as a
  limitation instead. Wrapping would require packaging the Bajutsu run inside an Instrumentation test
  APK. It would give up the out-of-band driver model, which is at odds with the deterministic-core
  design and the natural-language scenario hub.
- **Keep the adapter in core rather than an optional extra.** Rejected. It pulls the Google Cloud
  SDK/CLI into the deterministic dependency closure. The adapter ships behind `bajutsu[firebase]`.
- **Model Device Streaming as batch (like AWS Device Farm).** Rejected. Device Streaming is a *live*
  remote device, not a remote executor. Modelling it as batch would discard the seam that already
  fits. It stays live.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Adapter skeleton (`firebase-streaming`, `bajutsu[firebase]` extra, lazy import)
- [ ] Reservation + adb-over-SSL tunnel + streamed serial resolution
- [ ] Lease lifecycle (leak-free release on success / failure / interrupt)
- [ ] Cloud-difference hooks (boot-wait skip / `adb install` / capability degradation)
- [ ] Config (provider fields validated, loud on unknown/missing)
- [ ] Tests (faked CLI/tunnel boundary)
- [ ] Docs (Device Streaming how-to; Test Lab limitation stated)

## References

- [Android Device Streaming (adb over SSL)](https://developer.android.com/studio/run/android-device-streaming)
- [Firebase Test Lab](https://firebase.google.com/docs/test-lab)
- [BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md)
- [BE-0082 — capability preflight check](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md)
- Depends on sibling item: **device-cloud-provider-abstraction** (the seam this adapter implements)
