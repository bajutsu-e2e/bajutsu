**English** · [日本語](BE-0283-android-network-capture-ja.md)

# BE-0283 — Network-capture assertions for the Android backend

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0283](BE-0283-android-network-capture.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0283") |
| Implementing PR | [#1180](https://github.com/bajutsu-e2e/bajutsu/pull/1180) |
| Topic | Platform support |
| Related | [BE-0003](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) |
<!-- /BE-METADATA -->

## Introduction

This item ports `request` / `requestSequence` network-capture assertions to the adb [backend](../../docs/glossary.md#driver-backend-actuator-platform). It makes
two changes.

- It gives `BajutsuAndroid` an OkHttp interceptor. `BajutsuAndroid` is the Android peer of
  `BajutsuKit` and so far handles the clipboard alone.
  The interceptor reports each exchange to the in-process collector that the runner already starts
  for iOS.
- It bridges the emulator's isolated loopback back to that collector over `adb reverse`.

The assertion pipeline, the scenario schema, and the capability model need no change. The file
`bajutsu/capability_preflight.py` already treats `network` as a construct that no backend needs to
*advertise*. idb relies on the same accommodation today.

## Motivation

[BE-0003](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md) gave the
runner an in-app collector model for network observation. `BajutsuKit`'s `BajutsuURLProtocol`
intercepts `URLSession` traffic. It POSTs each exchange to a per-run `NetworkCollector`. The runner
starts that collector on `127.0.0.1:<port>` (`bajutsu/network.py`).

The `request` and `requestSequence` assertions, and the `until: { request }` waits
(`bajutsu/assertions/network.py`), then evaluate the collected exchanges. The model works without
extra plumbing. The iOS Simulator runs as a host process and shares the Mac's loopback.

[BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) shipped the adb backend with network
support explicitly out of scope, and the gap has stood since. `AndroidEnvironment`
(`bajutsu/platform_lifecycle/environments/android.py:172-179`) reports
`observes_network_via_driver() = False`. It also raises `NotImplementedError` from `hook_collector`,
the same as iOS.

The runner's device pool (`bajutsu/runner/pool.py:136-193`) does not know the difference. It still
pre-starts a `NetworkCollector` per Android device. It injects `BAJUTSU_COLLECTOR` and
`BAJUTSU_COLLECTOR_TOKEN` into the launch env, as it does for iOS. Nothing on Android has ever read
that env.

A `request` assertion against an Android target raises no error. It degrades to "zero
exchanges observed". That result reads as an app bug, not as an unimplemented backend feature.
[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md) found the same trust
problem in an advertised-but-broken capability. Here the problem lands on an evidence kind that
nothing gates in the first place.

Porting the mechanism is not a straight copy. The Android emulator does not share the host's
loopback the way the iOS Simulator does. Inside the emulator, `127.0.0.1` resolves to the emulator
itself. It does not resolve to the Mac that runs the collector.

This backend already has a working precedent for bridging that gap. `forward_cmd` and
`forward_remove_cmd` in `bajutsu/adb.py` (`bajutsu/adb.py:524-535`) tunnel a fixed device-side port
to the host. The resident UI Automator server
([BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md))
uses that tunnel. This item needs the same tunnel in the opposite direction. The device reaches out
to the host, and the host does not reach into the device. `adb reverse` provides that direction.

## Detailed design

### Work breakdown (MECE)

The five units are mutually exclusive and collectively exhaustive (MECE).

1. **`BajutsuAndroid`: an OkHttp interceptor that reports exchanges.** Add `BajutsuNet.kt` to
   `BajutsuAndroid/`. It is the Kotlin counterpart to `BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift`
   and `BajutsuURLProtocol.swift`.
   - iOS's `URLProtocol` swizzles itself into every `URLSessionConfiguration` transparently.
     Android has no single OS-level HTTP hook that reaches every client. The app under test adds
     one line, `.addInterceptor(BajutsuNet.interceptor())`, to its `OkHttpClient.Builder`.
   - The interceptor is a no-op unless `BAJUTSU_COLLECTOR` is present, mirroring
     `BajutsuNet.startIfEnabled()`.
   - The interceptor reads `BAJUTSU_COLLECTOR` and `BAJUTSU_COLLECTOR_TOKEN` from the intent
     extras. The launch env already delivers them (`bajutsu/adb.py:565-574`).
   - The interceptor POSTs each completed exchange as JSON shaped to match `NetworkExchange`
     (`bajutsu/network.py:29-47`). The JSON uses the same field names and aliases. The collector
     and the assertion pipeline therefore need no change.
   - The library stays app-agnostic. Every app embeds one library, and no per-app config exists.
     The clipboard receiver in `BajutsuAndroid` already set that precedent.

2. **bajutsu side: bridge the collector over `adb reverse`.** Add `reverse_cmd` and
   `reverse_remove_cmd` to `bajutsu/adb.py`, siblings of `forward_cmd` and `forward_remove_cmd`.
   - The commands run `adb reverse tcp:<port> tcp:<port>` and tear the tunnel down. The device-side
     port binds to the same host-side port that the `NetworkCollector` already picked.
   - The tunnel joins the Android lease lifecycle in `bajutsu/runner/pool.py`, alongside the
     existing `BAJUTSU_COLLECTOR` env injection (`pool.py:190-193`). The runner establishes the
     tunnel right before launch and releases it with the lease.
   - The env value already computed today (`http://127.0.0.1:<port>`) then resolves on-device with
     no URL rewrite.

3. **Confirm the capability model needs no change.** `bajutsu/capability_preflight.py:36-39`
   documents that `network` is deliberately ungated.
   - idb captures through the app-side collector without advertising the `network` capability.
     That token means *native* driver observation, which Playwright alone has.
   - The same reasoning covers adb once units 1 and 2 land. Nothing changes in
     `AdbDriver.CAPABILITIES` (`bajutsu/drivers/adb.py:542-554`).
   - Nothing changes in `KIND_CAPABILITY` / `resolve_evidence_providers`
     (`bajutsu/backends.py:339-409`) either. They implement the BE-0020 same-platform fallback,
     which stays irrelevant here because Android has no sibling actuator to fall back to.

   This unit is verification and not implementation. Recording the reasoning here keeps a future
   reader from "fixing" `AdbDriver` into wrongly advertising `network` as native.

4. **Test.** Cover three areas:
   - fast-gate tests for `reverse_cmd` and `reverse_remove_cmd` (command shape, and teardown
     pairing with the collector lifecycle)
   - fast-gate tests for the interceptor's JSON shape against `NetworkExchange` parsing
   - an on-device check that a `request` assertion against a real emulator-hosted app observes
     traffic end to end

   Like the other on-device Android checks, the on-device check stays off the fast Linux lane.

5. **Reconcile the showcase.** Embed `BajutsuNet`'s interceptor in the showcase Android app (debug
   builds only, matching the clipboard receiver's precedent). Extend an Android e2e scenario with a
   `request` assertion, so the feature has a running, checked example. The clipboard read-back in
   `device_android.yaml` plays the same role for BE-0233.

### Coverage bound

Capture covers OkHttp-originated HTTP(S) traffic alone. iOS has the same bounded shape, with its
`URLSession`-only scope (`BajutsuKit/README.md:47-51`). An app is out of scope here when its
networking goes through any of these:

- `HttpURLConnection` directly
- a different HTTP client
- a `WebView`

On iOS, `WKWebView` likewise needed its own follow-up
([BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md)).

## Alternatives considered

- **Bytecode- or classloader-level global interception**, to reach the same transparency as iOS's
  `URLProtocol` swizzle. Rejected. Android has no single, universal HTTP entry point the way
  `URLSessionConfiguration` is on iOS. OkHttp, `HttpURLConnection`, and third-party clients each
  need their own hook. A "capture everything" approach thus means broader instrumentation, with a
  larger, less deterministic surface (prime directive 2). An explicit, opt-in interceptor already
  delivers the benefit for the common case. That case is OkHttp, which Retrofit and most modern
  Android networking sit on top of.
- **A fixed emulator-only alias (`10.0.2.2`) instead of `adb reverse`.** Rejected. `10.0.2.2` is an
  emulator-only convention, and a physical device has no counterpart. The alias would fork the
  transport by target type. It would also duplicate work that `adb reverse` already does uniformly,
  as `forward_cmd` already does for the resident UI Automator server (BE-0245).
- **Ship `mocks` (deterministic stub responses) in this same item**, matching the full iOS
  `BajutsuMocks` parity in one pass. Deferred to a follow-up item. Serving a stub response needs the
  interceptor to *replace* an exchange, and observing alone does not. That is a materially different
  piece of design. Including it would widen this item's review surface for no gain to the
  observation half. BE-0003 itself shipped its own hardening (BE-0115, BE-0130) as separate, later
  items and not in one pass.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — add `BajutsuNet.kt`'s OkHttp interceptor to `BajutsuAndroid`.
- [x] Unit 2 — bridge the collector with `adb reverse` (`reverse_cmd`/`reverse_remove_cmd`, wired
  into the Android lease lifecycle via the `bridge_collector` seam).
- [x] Unit 3 — confirm and record that the capability model needs no change.
- [x] Unit 4 — add fast-gate and on-device test coverage.
- [x] Unit 5 — reconcile the showcase Android app (OkHttp migration) and its `network_android.yaml`
  e2e scenario (the `network (adb)` job in `android-e2e.yml`).

## References

[BE-0003 — codegen, traces, network & CI (M3)](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md),
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0245 — Resident UI Automator server for adb reads](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md),
[BE-0233 — adb clipboard on-device fidelity](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md),
[BE-0037 — WebView / hybrid support](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md),
`BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift`, `BajutsuKit/Sources/BajutsuKit/BajutsuURLProtocol.swift`,
`BajutsuAndroid/src/main/java/dev/bajutsu/android/Bajutsu.kt`,
`bajutsu/network.py`, `bajutsu/assertions/network.py`, `bajutsu/adb.py`,
`bajutsu/platform_lifecycle/environments/android.py`, `bajutsu/runner/pool.py`,
`bajutsu/capability_preflight.py`
