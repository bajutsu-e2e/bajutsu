**English** · [日本語](BE-0283-android-network-capture-ja.md)

# BE-0283 — Network-capture assertions for the Android backend

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0283](BE-0283-android-network-capture.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0283") |
| Topic | Platform support |
| Related | [BE-0003](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) |
<!-- /BE-METADATA -->

## Introduction

This item ports `request` / `requestSequence` network-capture assertions to the adb backend. It
gives `BajutsuAndroid` (the Android peer of `BajutsuKit`, so far clipboard-only) an OkHttp
interceptor that reports each exchange to the same in-process collector the runner already starts
for iOS, and it bridges the emulator's isolated loopback back to that collector over `adb
reverse`. The assertion pipeline, the scenario schema, and the capability model all need no
change: `bajutsu/capability_preflight.py` already treats `network` as a construct no backend needs
to *advertise* to satisfy, exactly the accommodation idb relies on today.

## Motivation

[BE-0003](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md) gave the
runner an in-app collector model for network observation: `BajutsuKit`'s `BajutsuURLProtocol`
intercepts `URLSession` traffic and POSTs each exchange to a per-run `NetworkCollector` the runner
starts on `127.0.0.1:<port>` (`bajutsu/network.py`), which `request` / `requestSequence` assertions
and `until: { request }` waits (`bajutsu/assertions/network.py`) then evaluate. This works without
extra plumbing because the iOS Simulator runs as a host process and shares the Mac's loopback.

[BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) shipped the adb backend with
network support explicitly out of scope, and that gap has stood since: `AndroidEnvironment`
(`bajutsu/platform_lifecycle/environments/android.py:172-179`) reports
`observes_network_via_driver() = False` and raises `NotImplementedError` from `hook_collector`, the
same as iOS. The runner's device pool (`bajutsu/runner/pool.py:136-193`) does not know the
difference. It still pre-starts a `NetworkCollector` per Android device and injects
`BAJUTSU_COLLECTOR` / `BAJUTSU_COLLECTOR_TOKEN` into the launch env exactly as it does for iOS.
Nothing on Android has ever read that env. A `request` assertion against an Android target
therefore does not fail loudly; it silently degrades to "zero exchanges observed," which reads as
an app bug rather than an unimplemented backend feature. This is the same trust problem
[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md) found in an
advertised-but-broken capability, now on the side of an evidence kind nothing gates in the first
place.

Porting the mechanism is not a straight copy, because the Android emulator does not share the
host's loopback the way the iOS Simulator does: `127.0.0.1` inside the emulator resolves to the
emulator itself, not to the Mac running the collector. Bridging that gap has a working precedent
already in this backend: `bajutsu/adb.py`'s `forward_cmd` / `forward_remove_cmd`
(`bajutsu/adb.py:524-535`) tunnel a fixed device-side port to the host for the resident UI Automator
server ([BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)).
This item needs the same tunnel in the opposite direction — the device reaching out to the host,
not the host reaching into the device — which `adb reverse` provides.

## Detailed design

### Work breakdown (MECE)

1. **`BajutsuAndroid`: an OkHttp interceptor that reports exchanges.** Add `BajutsuNet.kt` to
   `BajutsuAndroid/`, the Kotlin counterpart to `BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift` and
   `BajutsuURLProtocol.swift`. Unlike iOS's `URLProtocol`, which swizzles itself into every
   `URLSessionConfiguration` transparently, Android has no single OS-level HTTP hook that reaches
   every client — so the app under test adds one line, `.addInterceptor(BajutsuNet.interceptor())`,
   to its `OkHttpClient.Builder`. The interceptor is a no-op unless `BAJUTSU_COLLECTOR` is present
   (mirroring `BajutsuNet.startIfEnabled()`), reads `BAJUTSU_COLLECTOR` / `BAJUTSU_COLLECTOR_TOKEN`
   from the intent extras the launch env already delivers (`bajutsu/adb.py:565-574`), and POSTs each
   completed exchange as JSON shaped to match `NetworkExchange` (`bajutsu/network.py:29-47`) — same
   field names and aliases, so the collector and the assertion pipeline need no change. This keeps
   the library app-agnostic (one library every app embeds, not per-app config), the same precedent
   `BajutsuAndroid`'s clipboard receiver already set.

2. **bajutsu side: bridge the collector over `adb reverse`.** Add `reverse_cmd` /
   `reverse_remove_cmd` to `bajutsu/adb.py`, siblings of `forward_cmd` / `forward_remove_cmd`, that
   run `adb reverse tcp:<port> tcp:<port>` (device-side port bound to the same host-side port the
   `NetworkCollector` already picked) and tear it down. Wire the tunnel into the Android lease
   lifecycle in `bajutsu/runner/pool.py` alongside the existing `BAJUTSU_COLLECTOR` env injection
   (`pool.py:190-193`) — established right before launch, released with the lease — so the env value
   already computed today (`http://127.0.0.1:<port>`) resolves correctly on-device with no URL
   rewrite.

3. **Confirm the capability model needs no change.** `bajutsu/capability_preflight.py:36-39`
   already documents that `network` is deliberately ungated because idb captures via the app-side
   collector without advertising the `network` capability (that token means *native* driver
   observation, which only Playwright has). The same reasoning covers adb once units 1–2 land: no
   change to `AdbDriver.CAPABILITIES` (`bajutsu/drivers/adb.py:542-554`) or to
   `KIND_CAPABILITY`/`resolve_evidence_providers` (`bajutsu/backends.py:339-409`, the BE-0020
   same-platform fallback, which stays irrelevant here since Android has no sibling actuator to fall
   back to). This unit is verification, not implementation — record the reasoning here so a future
   reader does not "fix" `AdbDriver` into wrongly advertising `network` as native.

4. **Test.** Fast-gate coverage for `reverse_cmd`/`reverse_remove_cmd` (command shape, teardown
   pairing with the collector lifecycle) and for the interceptor's JSON shape against
   `NetworkExchange` parsing, plus an on-device check (gated off the fast Linux lane, like the other
   on-device Android checks) that a `request` assertion against a real emulator-hosted app observes
   traffic end to end.

5. **Reconcile the showcase.** Embed `BajutsuNet`'s interceptor in the showcase Android app (debug
   builds only, matching the clipboard receiver's precedent), and extend an Android e2e scenario
   with a `request` assertion so the feature has a running, checked example — the same role
   `device_android.yaml`'s clipboard read-back plays for BE-0233.

### Coverage bound

Capture is scoped to OkHttp-originated HTTP(S) traffic, the same bounded shape as iOS's
`URLSession`-only scope (`BajutsuKit/README.md:47-51`). An app whose networking goes through
`HttpURLConnection` directly, a different HTTP client, or a `WebView` is out of scope here, exactly
as `WKWebView` needed its own follow-up on iOS
([BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md)).

## Alternatives considered

- **Bytecode- or classloader-level global interception**, to reach for the same transparency as
  iOS's `URLProtocol` swizzle. Rejected: Android has no single, universal HTTP entry point the way
  `URLSessionConfiguration` is on iOS — OkHttp, `HttpURLConnection`, and third-party clients each
  need their own hook, so a "capture everything" approach means broader instrumentation with a
  larger, less deterministic surface (prime directive 2) for a benefit an explicit, opt-in
  interceptor already delivers for the common case (OkHttp, which Retrofit and most modern Android
  networking sit on top of).
- **A fixed emulator-only alias (`10.0.2.2`) instead of `adb reverse`.** Rejected: `10.0.2.2` is an
  emulator-only convention with no equivalent on a physical device, so it would fork the transport
  by target type and duplicate work `adb reverse` already does uniformly (as `forward_cmd` already
  does for the resident UI Automator server, BE-0245).
- **Ship `mocks` (deterministic stub responses) in this same item**, matching the full iOS
  `BajutsuMocks` parity in one pass. Deferred to a follow-up item: serving a stub response needs the
  interceptor to *replace* an exchange rather than only observe it, a materially different piece of
  design that would widen this item's review surface for no gain to the observation half; BE-0003
  itself shipped its own hardening (BE-0115, BE-0130) as separate, later items rather than in one
  pass.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — add `BajutsuNet.kt`'s OkHttp interceptor to `BajutsuAndroid`.
- [ ] Unit 2 — bridge the collector with `adb reverse` (`reverse_cmd`/`reverse_remove_cmd`, wired
  into the Android lease lifecycle).
- [ ] Unit 3 — confirm and record that the capability model needs no change.
- [ ] Unit 4 — add fast-gate and on-device test coverage.
- [ ] Unit 5 — reconcile the showcase Android app and its e2e scenario.

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
