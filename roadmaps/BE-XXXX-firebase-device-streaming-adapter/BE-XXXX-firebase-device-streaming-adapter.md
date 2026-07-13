**English** · [日本語](BE-XXXX-firebase-device-streaming-adapter-ja.md)

# BE-XXXX — Firebase Test Lab / Device Streaming adapter

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-firebase-device-streaming-adapter.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

This item adds the first concrete **device provider** on top of the *device-cloud-provider-
abstraction* seam: running a Bajutsu Android scenario against a Google-hosted real device through
**Firebase's Android Device Streaming**, which exposes a reserved device as "adb over SSL." Because
the Android backend already drives any reachable `adb` serial — including an `IP:port`
`adb connect` target — the adapter's job is narrow: reserve the device, establish the adb-over-SSL
connection, hand the runner a serial, and release the reservation afterwards.

## Motivation

Firebase is a natural first target: many teams already use it, and Device Streaming provides
exactly the shape the live-topology seam wants — a reserved real device reachable over adb. Landing
Firebase first also **validates the provider seam against a real service** before the abstraction is
frozen (the PoC-first sequencing the foundation item calls for).

A key finding from investigation, worth recording so the scope is not misread: **Firebase Test Lab
proper cannot host Bajutsu's driver.** Test Lab accepts only fixed test types — Instrumentation
(Espresso / UI Automator), Robo, and Game Loop — uploaded as APK/AAB via
`gcloud firebase test android run`. There is no path to upload an arbitrary Python driver and have
it drive the device out-of-band. Test Lab is a closed test runner, not an open execution sandbox.
The viable route to a live adb device on Google's cloud is therefore the **separate** Android Device
Streaming product (Android Studio's device reservation), which explicitly grants an "adb over SSL"
connection usable by "any tool that uses adb." This item targets Device Streaming, and documents the
Test Lab limitation rather than pretending to wrap it.

## Detailed design

Implement a `DeviceProvider` (kind `firebase-streaming`) as an **optional extra** (e.g.
`pip install "bajutsu[firebase]"`) that wraps the Firebase / Android Device Streaming CLI:

- **acquire(target)** — reserve a device (device model / API level / project from the target's
  `deviceProvider` config), establish the adb-over-SSL tunnel, wait until the streamed device shows
  ready in `adb devices`, and return a `DeviceLease` whose serial is the streamed `IP:port` target.
- **release()** — tear down the tunnel and end the reservation (stopping billing).

Everything downstream is unchanged: `make_driver("adb", serial)` drives the streamed device exactly
as it drives a local emulator, because the serial is the only thing the driver needs. The
cloud-difference hooks defined by the foundation item apply here: the device is already booted (skip
boot-wait), app install runs through the normal `adb install` over the tunnel (Device Streaming does
not pre-install), and emulator-only device-control primitives that the streamed device lacks are
declared unsupported so preflight cuts them loudly.

### Work breakdown (MECE)

1. **Adapter skeleton** — `firebase-streaming` provider registered under the seam's registry,
   shipped behind the `bajutsu[firebase]` extra; import of the extra is lazy so the gate stays
   cloud-free.
2. **Reservation + adb-over-SSL** — reserve via the Device Streaming CLI, establish the tunnel,
   resolve the streamed serial, and confirm readiness through `adb devices` (a condition wait, no
   fixed sleep).
3. **Lease lifecycle** — reliable `release()` on success, failure, and interruption, so a
   reservation is never leaked (billing safety).
4. **Cloud-difference hooks** — wire boot-wait skip, normal `adb install`, and device-control
   capability degradation through the foundation's `RunEnvironment` hooks.
5. **Config** — provider-specific fields (project, device model, API level) validated by the
   adapter; unknown/missing → loud error.
6. **Tests** — fake the CLI/tunnel boundary (external process/network is the sanctioned mock point)
   and assert acquire→serial→release, readiness-as-condition, and lease cleanup on failure. No live
   Firebase in the gate.
7. **Docs** — a Firebase how-to in `docs/` (both languages) that states plainly: Device Streaming is
   the supported path; Test Lab proper is not wrappable.

### Prime-directive compliance

- **AI out of the gate.** The adapter only reserves/connects/releases; no model, no effect on the
  deterministic verdict.
- **Determinism first.** Readiness is a condition on `adb devices`, not a sleep; driving the streamed
  device is as reproducible as a local one.
- **App-agnostic.** All Firebase specifics live in the target's `deviceProvider` config and the
  optional adapter; the driver and runner are untouched.

## Alternatives considered

- **Wrap Firebase Test Lab proper (Instrumentation/Robo/Game Loop).** Would require packaging the
  Bajutsu run inside an Instrumentation test APK and giving up the out-of-band driver model — at odds
  with the deterministic-core design and the natural-language scenario hub. Rejected; documented as a
  limitation instead.
- **Keep the adapter in core rather than an optional extra.** Pulls the Google Cloud SDK/CLI into the
  deterministic dependency closure. Rejected — ship behind `bajutsu[firebase]`.
- **Model Device Streaming as batch (like AWS Device Farm).** It is a *live* remote device, not a
  remote executor; modelling it as batch would discard the seam that already fits. Kept it live.

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
