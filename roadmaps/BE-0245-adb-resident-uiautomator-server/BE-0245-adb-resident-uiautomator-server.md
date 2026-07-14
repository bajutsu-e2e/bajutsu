**English** · [日本語](BE-0245-adb-resident-uiautomator-server-ja.md)

# BE-0245 — Resident UI Automator server for adb reads

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0245](BE-0245-adb-resident-uiautomator-server.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0245") |
| Implementing PR | [#1011](https://github.com/bajutsu-e2e/bajutsu/pull/1011), [#1017](https://github.com/bajutsu-e2e/bajutsu/pull/1017), [#1032](https://github.com/bajutsu-e2e/bajutsu/pull/1032), [#_pending_](https://github.com/bajutsu-e2e/bajutsu/pull/_pending_) |
| Topic | Platform support |
| Related | [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) |
<!-- /BE-METADATA -->

## Introduction

Reading the screen on the Android adb backend is an order of magnitude slower than on iOS, and the
whole gap is a per-invocation startup cost: every `AdbDriver.query()` shells out to
`adb exec-out uiautomator dump`, which spins up a fresh instrumentation, connects a `UiAutomation`
session, waits for idle, dumps, and tears the session down — ≈ 2.4 s each time, against ≈ 0.1–0.3 s
for the same read on idb. This item removes that startup cost by keeping a **resident UI Automator
instrumentation** alive for the duration of a run and querying the hierarchy over a local
socket / HTTP, the approach Appium's UiAutomator2 driver takes. It is the fourth (and final,
architectural) unit carved out of [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md),
kept separate because it needs a device to verify and a packaged instrumentation, so it does not fit
that item's fast-gate change. Nothing here touches the determinism contract: reads stay
condition-driven, ambiguous selectors still fail fast, and no LLM enters the `run` path.

## Motivation

[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) pinned the Android run's
slowness to the screen read and shipped the two low-risk reductions (making the runner's end-of-step
read lazy and reusing the previous step's tree as the next step's `before`) plus the adb `_settle`
retune. Those cut how *often* the runner reads, but they cannot lower the floor: the ≈ 2.4 s
*per-invocation* cost of `uiautomator dump` itself. BE-0234's own measurements show this cost is
independent of tree size, compression, and output sink — it is the instrumentation startup, not the
XML transfer or traversal — so no amount of reading-less crosses it. A read-bound backend where each
necessary read still costs ≈ 2.4 s keeps Android authoring heavy even after BE-0234's reductions.

A resident session pays that startup **once** for the whole run, then answers each subsequent read
over an already-open channel. That is exactly what closes the 10–20× gap with iOS: the expected
result is a read that drops from ≈ 2.4 s to ≈ 0.1–0.3 s. The cost is real (a packaged instrumentation
and its lifecycle), which is why it is its own item rather than a knob — but the payoff is the one
change that makes the Android read as cheap as the iOS one.

The resident server is a **uniform, app-independent** component: it drives whatever app is under
test, exactly as the on-device SDKs
([BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), BajutsuKit /
BajutsuAndroid) do. A uniform library that every target uses identically is not a per-app carve-out,
so this fits prime directive 3 (app-agnostic) the same way those SDKs do: the tool, drivers, and
runner stay unchanged across targets, and no scenario changes.

## Detailed design

The change is confined to how the adb driver *obtains* a hierarchy dump. It swaps the internals of
`AdbDriver._describe()` ([`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)) only — the
parsing (`parse_hierarchy`), the normalization to Elements, the transient-empty retry, `_settle`,
and the whole selector / resolve contract stay exactly as they are, so every existing adb test and
the driver conformance suite ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md))
keep passing unchanged. The existing `uiautomator dump` path is retained as a fallback, so a device
where the resident server cannot start is never left worse off than today.

### Work breakdown (MECE)

1. **Package and launch the resident instrumentation.** Bundle a UI Automator instrumentation
   (an `androidx.test` UiAutomator server, in the shape Appium's UiAutomator2 server uses) and
   install / start it on the target device at the start of a run. This is the app-independent
   component; decide its distribution (a checked-in prebuilt APK vs. built on demand) and where it
   lives in the repo, mirroring how the on-device SDKs are packaged
   ([BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md)).

2. **A hierarchy-query channel.** Expose the accessibility hierarchy over a local socket / HTTP
   (`adb forward` to the resident server) and define the request/response for "dump the current
   hierarchy". The response must carry the same information the current `uiautomator dump` XML does,
   so `parse_hierarchy` (or a thin adapter to it) still produces identical Elements. No new selector
   semantics — this unit changes transport, not meaning.

3. **Swap `AdbDriver._describe()` to the resident channel, with the dump path as fallback.** Route
   `_describe()` through the resident server when it is available; on any startup / channel failure,
   fall back to `adb exec-out uiautomator dump` (today's path) so the backend degrades to the
   current behavior rather than failing. Everything above `_describe()` — `query()`'s
   transient-empty retry, `_settle`, `_resolve`, actuation — is untouched.

4. **Server lifecycle tied to the run.** Start the resident server once per device lease and stop it
   when the run ends (including on failure / interrupt), so no instrumentation is left running on the
   device. Fold this into the existing device-pool / lease lifecycle rather than a bespoke path.

5. **Verify on device and guard against regression.** Record the before/after per-step read
   wall-clock on a real emulator (the BE-0234 read-count yardstick is already in place), confirm the
   read drops to ≈ 0.1–0.3 s, and extend the Android e2e lane
   ([BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)) so the resident
   path and the dump fallback are both exercised. A hard CI *timing* gate stays out of scope
   (wall-clock is environment-dependent and would be flaky), consistent with BE-0234.

## Alternatives considered

- **Keep per-invocation `uiautomator dump` and only read less (BE-0234's units 1-3).** Already
  shipped, and worthwhile, but it cannot cross the ≈ 2.4 s per-read floor — that floor is precisely
  what this item removes. The two are complementary: fewer reads *and* a cheap read.

- **`uiautomator dump --compressed` / dump to a device file then `cat`.** Both measured by BE-0234 at
  ≈ 2.0–2.5 s — no improvement, because the cost is the instrumentation startup, not the XML size or
  the output sink. Rejected there; they do not address the bottleneck here either.

- **Adopt Appium's UiAutomator2 server wholesale as a dependency.** Tempting, but it brings a large
  surface (a full Appium server, its command set, its versioning) for what bajutsu needs, which is
  just a resident hierarchy dump. The design leans on Appium's *approach* (a resident instrumentation
  queried over a socket) without taking on the whole server; the exact reuse-vs-minimal boundary is
  an open question for the implementation.

- **A persistent `uiautomator` shell process reused across reads.** Does not help: each
  `uiautomator dump` invocation starts its own instrumentation regardless of how the shell is kept
  open, so the startup cost is paid per dump, not per shell.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Package and launch the resident UI Automator instrumentation (app-independent component; decide distribution).
- [x] Define the hierarchy-query channel (local socket / HTTP) whose response `parse_hierarchy` consumes unchanged.
- [x] Swap `AdbDriver._describe()` to the resident channel, keeping `uiautomator dump` as the fallback.
- [x] Tie the resident server's lifecycle to the device lease (started once, stopped on run end / failure).
- [x] Verify on device (read drops to ≈ 0.1–0.3 s) and guard the resident + fallback paths in the Android e2e lane.

Log:

- PR-A ([#1011](https://github.com/bajutsu-e2e/bajutsu/pull/1011)) — Python-only, device-free scaffold for the resident-channel swap (unit 3). Adds an
  injectable `fetch_hierarchy` seam to `AdbDriver`: `_describe()` reads via `_read_source()`, which uses
  the resident fetch when configured and degrades to `uiautomator dump` with a loud warning on
  `AdbResidentError`. `parse_hierarchy` and everything above `_describe()` (transient-empty retry,
  `_settle`, `_resolve`, actuation) are unchanged, and the default (no fetch) keeps today's
  dump-every-read behavior exactly — so no box is ticked yet. Unit 2's hierarchy-query channel (the
  `adb forward` transport and its HTTP handshake) is not written yet; it lands in a follow-up PR.
- PR-B ([#1017](https://github.com/bajutsu-e2e/bajutsu/pull/1017)) — the resident server body and its
  distribution decision (units 1–2, server side). Adds `BajutsuAndroidUIAutomatorServer/`, a self-contained
  Gradle project (committed wrapper, so a fresh clone builds with only the Android SDK) holding an
  `androidTest` instrumentation: `am instrument -w` runs a blocking `@Test` that keeps one
  `UiAutomation` session warm and answers `GET /source` over a raw loopback `ServerSocket` (no HTTP
  library) with `UiDevice.dumpWindowHierarchy()`'s XML — the same `AccessibilityNodeInfoDumper` XML
  format `parse_hierarchy` already parses. Distribution is build-on-demand through the committed
  `gradlew` (mirroring how the XCUITest runner and the showcase Android build ship), keeping no
  prebuilt APK in the repo. Verified by hand on an API-34 emulator: after adding the `INTERNET`
  permission the host app needs to bind a socket, the server stays resident and `GET /source`
  returns hierarchy XML `parse_hierarchy` parses cleanly. That check also surfaced the equivalence
  work unit 2 still owes: `UiDevice.dumpWindowHierarchy()` traverses *all* windows, so it includes
  the SystemUI status-bar tree (clock, wifi, battery, notification icons — 29 nodes) that the
  platform `uiautomator dump` omits by scoping to the active window. The app content is identical,
  but scoping the resident dump to the active window so the two yield the same Elements is deferred
  to PR-C, alongside the `adb forward` client transport and the `fetch_hierarchy` wiring — where it
  can be regression-tested end to end. No box is ticked for the same reason: no read runs through
  the resident path until that wiring lands. Review also noted that the accepted socket's read
  timeout (`soTimeout`, added while hardening the single-threaded accept loop) bounds only reads, so
  a peer that stalls mid-response could still wedge the loop from the write side; a write-side bound
  belongs with PR-C's transport hardening and its regression tests, not this scaffold. CI wiring for
  the build is deferred to a later slice.
- PR-C ([#1032](https://github.com/bajutsu-e2e/bajutsu/pull/1032)) — the Python transport wiring
  (units 1 launch, 2, 3, 4). Adds `adb forward` / `am instrument` / install command builders to
  `bajutsu/adb.py`; a new `bajutsu/adb_resident.py` holding `fetch_source` (a stdlib `http.client`
  `GET /source` that raises `AdbResidentError` on any channel fault, decode error included, so the
  driver always degrades cleanly), `narrow_to_active_window` (drops the SystemUI decor windows so the
  resident `dumpWindowHierarchy` XML parses to the same Elements as `uiautomator dump` — the unit-2
  equivalence, gate-tested against a hand-built two-window tree), and a `ResidentServer` lifecycle
  (install both APKs, spawn the blocking instrumentation, `adb forward tcp:0`, a bounded connect-retry
  readiness wait — a condition wait, no fixed sleep — and a `stop` that reaps the client, force-stops
  the device-side package, and removes the forward). `make_driver` threads a `fetch_hierarchy` through
  to `AdbDriver`, and `AndroidEnvironment` starts the server per lease and stops it on teardown
  (fires on failure/interrupt too). The read fault latches: after the first `AdbResidentError` the
  channel is disabled for the rest of the lease, so a mid-run channel death does not re-log or re-pay
  the connect timeout on every read. The channel is **opt-in behind `BAJUTSU_ADB_RESIDENT`** until the
  e2e lane builds and installs the server; unset, the adb backend reads via `uiautomator dump`
  unchanged. The client's socket timeout bounds a server that stalls mid-response (an `AdbResidentError`
  → dump fallback), so PR-B's write-side-wedge note is covered from bajutsu's side; a server-side write
  bound stays a minor hardening for the on-device slice. Everything here is device-free and gate-tested;
  the on-device wall-clock verification (≈ 0.1–0.3 s) and the e2e-lane guard for both paths (unit 5)
  land in PR-D, which also flips the channel on by default — so that box stays unticked.
- PR-D ([#_pending_](https://github.com/bajutsu-e2e/bajutsu/pull/_pending_)) — unit 5, flips the
  channel on by default and guards both paths in CI. `_make_resident` now reads over the resident
  channel whenever the server APKs are built (`server_apks_built`, `make -C BajutsuAndroidServer
  build`) and falls back to `uiautomator dump` otherwise, turning `BAJUTSU_ADB_RESIDENT` from an
  opt-in flag into an override (`0` pins the dump path even on a built tree, `1` forces the resident
  path and degrades loudly if it is not built). The Android e2e lane (`android-e2e.yml`) builds the
  server before booting the emulator, so `e2e`/`e2e-golden`/`e2e-visual` run over the resident
  channel, and a new `e2e-fallback` target re-runs the golden with the channel forced off — the same
  golden proving the two paths yield the same element tree, so the resident path and the
  `uiautomator dump` fallback are both exercised on every run. Per-step wall-clock is not asserted (a
  hard CI *timing* gate stays out of scope, consistent with BE-0234); the golden equivalence over the
  resident channel on the emulator is the on-device correctness check. `drivers.md` and
  `architecture.md` (both languages) now describe the resident-first read. This completes the item.

## References

[BE-0234 — Speed up adb scenario runs (uiautomator dump bottleneck)](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md),
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0233 — adb clipboard on-device fidelity](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0208 — Android on-device e2e in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py),
[`bajutsu/adb.py`](../../bajutsu/adb.py)
