**English** · [日本語](BE-XXXX-adb-unrooted-multitouch-ja.md)

# BE-XXXX — Unrooted pinch and rotate on the adb driver through the resident server

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-adb-unrooted-multitouch.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | TBD — filled in once the PR is open |
| Topic | Platform support |
| Related | [BE-0232](../BE-0232-adb-multitouch-gestures/BE-0232-adb-multitouch-gestures.md), [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) |
<!-- /BE-METADATA -->

## Introduction

We let the Android (adb) driver perform `pinch` and `rotate` on a device without root. The resident
UI Automator server gains a two-pointer gesture endpoint. The endpoint injects both fingers with
`UiAutomation.injectInputEvent`, which needs no root. The driver sends a two-finger gesture there
first. The raw `sendevent` sweep stays as the fallback for a rooted device whose server is down.

The change closes a gap that today fails both steps on every production phone. A driver that
declares the `multiTouch` capability will be able to honor it on an unrooted device.

## Motivation

A two-finger gesture on Android today depends on root. The adb driver writes the two contacts as
raw kernel touch events with `sendevent`, and writing to `/dev/input` needs root
([BE-0232](../BE-0232-adb-multitouch-gestures/BE-0232-adb-multitouch-gestures.md)). On an unrooted
device, `pinch` and `rotate` fail with `UnsupportedAction`. No production phone runs with root, and
few devices in a device cloud do. The driver still declares `multiTouch`. Preflight therefore admits
a scenario that then fails at its first two-finger step.

BE-0232 saw the way out and set it aside. Its alternatives named on-device UI Automator
instrumentation as "a documented future option for lifting the root precondition". BE-0232
rejected that option because it needed a device-side test Android package (APK) to build and
install. The resident
UI Automator server
([BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md))
is now that APK. The server already runs on every lease that builds it, and it already injects a
double tap through `UiAutomation.injectInputEvent` without root. One more endpoint lets it inject
two pointers as well.

The outcome is observable in CI. The shared `gestures_multitouch.yaml` scenario will pass on the
Android emulator after `adb unroot`, where it fails with `UnsupportedAction` today. Each pinch and
rotate record in the run's actuation log will also name the route the gesture took. A reader can
then tell from the evidence alone whether a run used root.

## Detailed design

The host keeps every decision it makes today. It resolves the target and derives both contacts with
the existing `pinch_contacts` and `rotate_contacts` helpers. Only the injector changes: the host
hands the four contact points to the device, in the same tree-space pixels it already reads.

### Work breakdown (MECE)

1. **Server endpoint.** `BajutsuAndroidUIAutomatorServer` gains `POST /gesture`. The request names
   both contacts' start and end points, a duration, and a step count. A missing or malformed field
   answers `400`, never a default. The server injects one two-pointer sequence: the first finger
   down, the second finger down, the interleaved moves, and both fingers up. Each event carries a
   stated event time, as the server's double tap does. The reply is `200` when the platform accepted
   every event. On the first rejected event the server stops, cancels any pointer still down with
   `ACTION_CANCEL`, and answers `500`.
2. **Python client.** `adb_resident.gesture` sits next to `act` and `type_text` and maps faults the
   same way. A `404` from an older server raises `AdbActUnsupported`. A reply lost after the request
   went out raises `AdbActUncertain`. Any other fault raises `AdbResidentError`. The resident channel
   carries the call to the driver, as it carries `type_text`.
3. **Driver routing.** `pinch` and `rotate` try the server first. A rooted device falls back to the
   `sendevent` sweep when the server is absent or predates the endpoint. Nothing reached the screen in
   either case. A rejected gesture fails the step instead, because its first events may have landed.
   A lost reply never falls back either, for the same reason. A device with neither route fails with
   an `UnsupportedAction` that names both preconditions.
4. **Actuation log.** A gesture the server injected records `via: resident`, a new channel token. A
   gesture the `sendevent` sweep injected keeps `via: coordinate`. Both carry the same anchor point,
   frame, and scale or angle. The new token therefore joins `coordinate` as a channel that carries
   points: the host still aims the gesture, and the token records which injector delivered it.
5. **Unit tests.** The fast suite covers the client's status mapping, the routing order, each
   fallback, the lost-reply case, and the error that names both preconditions.
6. **On-device proof.** The Android `e2e` target runs `gestures_multitouch` once more after
   `adb unroot`, so CI proves the unrooted route on every run.
7. **Docs.** `docs/platform-parity.md` drops its `pinch` and `rotate` row, and `docs/drivers.md`
   describes both routes. Both changes land in English and Japanese. BE-0232 records that its
   future option has landed.

### Out of scope

The single-finger `sendevent` double tap keeps its own route. The resident `/act` endpoint already
serves a double tap without root, so that path is no longer the common case.

## Alternatives considered

- **Keep `pinch` and `rotate` root-only.** Rejected. The gap stays open on every real device, while
  the server that closes it already runs on the lease.
- **Replace `sendevent` entirely.** Rejected for now. The `sendevent` sweep is the one route that
  works when the server is unavailable on a rooted device. We keep both until the server route has
  its own CI record.
- **Address the contacts by element identity, as `/act` does.** Rejected. The host derives four
  points from one frame, a scale, and an angle. Sending an identity would move that geometry to the
  device and duplicate it in Kotlin, with no gain: a resolved frame already drives today's route.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Server endpoint `POST /gesture` in `BajutsuAndroidUIAutomatorServer`.
- [x] Python client `adb_resident.gesture`, carried by the resident channel.
- [x] Driver routing: server first, rooted `sendevent` second, a two-precondition error otherwise.
- [x] Actuation log records the route (`resident` or `coordinate`).
- [x] Unit tests for the client mapping and the routing order.
- [x] Unrooted `gestures_multitouch` run in the Android `e2e` target.
- [x] Docs (en + ja) and the BE-0232 note.

## References

- [BE-0232 — Multi-touch gestures on the adb driver](../BE-0232-adb-multitouch-gestures/BE-0232-adb-multitouch-gestures.md)
- [BE-0245 — Resident UI Automator server](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)
- [Issue #2391](https://github.com/bajutsu-e2e/bajutsu/issues/2391) — the gap report this item answers.
- [`docs/platform-parity.md`](../../docs/platform-parity.md)
