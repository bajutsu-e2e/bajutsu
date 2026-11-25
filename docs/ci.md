# Continuous integration

This document covers two separate topics:

1. **CI for this repo**: guarding the tool itself (`.github/workflows/`).
2. **Running bajutsu in *your* app's CI**: a composite action and a recipe you reuse.

## This repo's CI

The table lists each workflow with its runner and its triggers. The sections after it describe what each workflow does, in the same order.

| Workflow | Runner | When |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) (`check` job) | Linux | push to `main`, every PR (pull request) |
| [`mcp-wire.yml`](../.github/workflows/mcp-wire.yml) | Linux | manual + PRs touching `bajutsu/mcp/**`, the `bajutsu mcp` CLI, or the wire test |
| [`web-e2e.yml`](../.github/workflows/web-e2e.yml) | Linux | manual + every PR + merge queue (required `E2E (web)` check) |
| [`dependency-audit.yml`](../.github/workflows/dependency-audit.yml) | Linux | manual + weekly + push to `main` / PRs touching `pyproject.toml` / `uv.lock` |
| [`swift.yml`](../.github/workflows/swift.yml) | macOS | push to `main` + PRs touching `BajutsuKit/**` |
| [`ios-e2e.yml`](../.github/workflows/ios-e2e.yml) | macOS | manual + every PR + merge queue (required `E2E (iOS)` check) |
| [`android-e2e.yml`](../.github/workflows/android-e2e.yml) | Linux | manual + every PR + merge queue (required `E2E (android)` check) |
| [`devicefarm.yml`](../.github/workflows/devicefarm.yml) | Linux | **manual only** (`workflow_dispatch`) |
| [`ai-smoke.yml`](../.github/workflows/ai-smoke.yml) | Linux | **manual only** (`workflow_dispatch`) |

### `ci.yml`: the Python gate

The `check` job runs the full `make check` gate on Python 3.13:

- lockfile freshness (`uv lock --check`)
- formatting (`ruff format --check`)
- lint (`ruff`)
- shell lint (`shellcheck`)
- workflow lint (`actionlint`)
- types (`mypy bajutsu demos scripts`, plus `mypy --allow-untyped-defs tests` — BE-0388)
- `pytest` with a coverage floor (`--cov-fail-under=89`)

The logic layer needs no Simulator, so the job is fast and cheap.

### `mcp-wire.yml`: the MCP wire round-trip

This workflow is the **MCP wire round-trip** lane (BE-0301). It has one job, **wire (stdio)**.

The job starts `bajutsu mcp --transport stdio` as a real subprocess. The `mcp` SDK's `fastmcp.Client` drives the subprocess over stdio. The command is `pytest tests/test_mcp_wire.py -m mcp_wire -n0`.

The job exercises three behaviors that the in-process `tests/test_mcp.py` never touches:

- the JSON-RPC framing
- the tool-schema advertisement
- the resource-URI encoding

Concretely, it lists tools and round-trips a `bajutsu_doctor` / `bajutsu_run` call. It also reads a run-evidence resource. Every check runs against the `fake` backend.

- `bajutsu_doctor` runs real device-free logic in the server.
- `bajutsu_run` spawns a real `bajutsu run` whose verdict depends on the environment. The round-trip asserts that the verdict line survives the transport, and nothing more.

The `mcp_wire` marker keeps the suite out of the fast gate (pyproject `addopts` `not mcp_wire`). A real subprocess with stdio inter-process communication (IPC) is more timing-sensitive. An in-process call is not. The lane thus lands as a **non-gating** per-PR signal. It becomes required once stable, the precedent BE-0282's `network (playwright)` set.

### `web-e2e.yml`: the web (Playwright) backend lane

This workflow is the **web (Playwright) backend** lane (BE-0279). It runs six jobs against a headless Chromium:

- **smoke (playwright)** runs the `demos/web` scenarios (`make -C demos/web e2e`).
- **dogfood (serve UI)** drives Bajutsu's own serve SPA (BE-0058).
- **conformance (playwright)** runs the BE-0114 driver contract against the real browser.
- **network (playwright)** drives the real network path (`make -C demos/web e2e-network`, BE-0282). It covers `page.route` interception, `requestfinished` capture, and the `mocked` flag. It also covers redaction of evidence captured for real.
- **codegen (playwright)** generates a native Playwright test from a scenario (BE-0293). The command is `make -C demos/web codegen-e2e`. The real `@playwright/test` runner runs the test. The job is the web twin of `ios-e2e.yml`'s `codegen (xcuitest)`.
- **onboarding (doctor / provision)** exercises `bajutsu.common.provisioning.provision` end to end. It runs against a genuinely browserless host. It then re-checks `doctor`'s environment gate after provisioning (BE-0304).

The lane needs **no Mac / Simulator**, so it proves the core is platform-neutral.

The same `changes` detector path-gates all six jobs (`scripts/e2e_changes.py`, `E2E_LANE=web`). Five jobs are deterministic and host-independent: every job but **onboarding**. Those five feed the always-reporting `E2E (web)` job, the required check. **onboarding** stays a per-PR signal (BE-0304). A PR that cannot affect the web path skips the browser jobs, and the gate passes.

**network (playwright)** and **codegen (playwright)** each landed as a per-PR signal first. Both proved stable in CI and now sit in `E2E (web)`'s `needs:`. **network** is the web twin of `android-e2e.yml`'s already-gating `network (adb)`. **codegen** is the web twin of `ios-e2e.yml`'s already-gating `codegen (xcuitest)`.

### `dependency-audit.yml`: the dependency audit

This workflow audits the locked dependency graph (`uv export` → `pip-audit --no-deps`). It checks the graph against the advisory database.

The result depends on the lockfile and the database alone. The workflow thus runs on a dependency change. It also runs on a weekly schedule, which catches advisories newly disclosed against unchanged pins.

### `swift.yml`: BajutsuKit unit tests

This workflow runs `swift build` and `swift test` for [BajutsuKit](../BajutsuKit). It unit-tests the pure-Foundation logic (request matching and mock parsing) with no Simulator. `ios-e2e.yml` covers the on-device interception itself.

### `ios-e2e.yml`: the iOS (XCUITest) backend lane

This workflow is the **iOS (XCUITest) backend** lane. It runs ten macOS jobs against the showcase. Every job uses the XCUITest backend (the resident BajutsuRunner).

#### iOS build and setup

- **build (app + runner)** compiles both showcase apps (SwiftUI and UIKit) and the resident runner once. It uploads them as a single `ios-build` artifact.
- The consumer jobs download and install those products rather than rebuilding. The lane builds Swift cold once rather than once per job.
- Each consumer fetches the artifact through the [`download-ios-build`](../.github/actions/download-ios-build/action.yml) action. The action retries the download up to twice with a short backoff. One transient network error no longer fails the job before a scenario runs. The error comes from the artifact service.
- A shared `setup-ios-toolchain` composite action collapses the steps every macOS job repeats. Those steps are Xcode, uv, xcodegen, and Simulator boot. The `build` job passes `boot: false`, since it needs no Simulator.

#### iOS jobs

**run (xcuitest)** is the homogeneous gating lane. It folds the scenarios of four former jobs into one `bajutsu run`. The former jobs were `smoke`, `gestures`, `permission`, and `runner-actuation`. The run warm-reuses the resident runner across the scenarios (BE-0291).

The lane caps reuse at one per runner (`BAJUTSU_XCUITEST_MAX_WARM_REUSES: 1`). The cap curbs a mid-run runner crash on contended CI hosts.

The twelve scenario documents that run are every scenario the seven files hold. BE-0315 made the notification grant native, so nothing is `ai`-tagged to exclude. The documents pay six cold `xcodebuild test-without-building` startups rather than one. That is two more than the four separate jobs they replaced summed to. Each of those jobs also paid its own Swift build and boot. The shared `build` job now pays that cost once for the whole lane.

The scenarios cover:

- the Stable catalog (`smoke.yaml`)
- the pinch/rotate two-finger gesture (`gestures_multitouch.yaml`, BE-0019)
- the runner channel's `/type` (`search.yaml`) and `/swipe` + `/back` (`notices.yaml`, BE-0281)
- each button of a presented `UIAlertController` (`alert.yaml`). The scenario alone reaches `uniquelyIdentifiedElement`'s duplicate-group branch. `swift test` cannot compile that branch.
- `permission.yaml`: the deterministic `permissions` field. Since BE-0315 it also covers the native reactive `systemAlertHandling` notification grant. It covers BE-0276 location and BE-0315 notification, both now on the gate.
- the proactive `handleSystemAlert` step (`permission_system_alert.yaml`, BE-0316). It taps the "Allow" button of the SpringBoard notification prompt. The tap uses an accessibility query and no vision model.

`run` runs each scenario to its own verdict. It reddens the gate if any scenario fails. The per-scenario pass/fail thus moves from job names into the run's report.

**actuation (xcuitest)** is the lane's non-gating landing pad. It drives on-device XCUITest actuation that `run` has not absorbed yet (BE-0281):

- `back` (`navigation.yaml`)
- device control (`setLocation` / clipboard / `push`, `device.yaml` + `push.yaml`) on the Stable launch tab
- the foreground notification banner that the interruption monitor clears (`push_banner.yaml`, BE-0416)
- the four text-editing actuators (`text_editing.yaml`)

It also drives the scenario-authoring features on a third actuating backend (BE-0285):

- `extract` capturing the Log tab's live counter (`extract.yaml`)
- `forEach` iterating the five Stable rows (`foreach.yaml`). The loop body pushes and pops a detail, so the tree mutates between iterations.
- data-driven rows (`data_driven.yaml`)
- `relaunch` (`relaunch.yaml`)

Those four are the shared showcase files, unchanged (BE-0221). The list puts them ahead of the device-control scenarios. Neither a delivered notification nor an OS-level grant then precedes them.

Two scenarios come last in the list.

`system_alert_handling.yaml` checks one claim. The reactive `systemAlertHandling.rules` guard alone answers three prompts. They are notification, tracking, and paste. The scenario uses no `handleSystemAlert` step. The file covers notifications denied. It also covers tracking and paste, each granted and denied. The deny scenarios show that the rule answered. XCUITest's own default handler taps the default button. That handler cannot produce a denied state. As new on-device coverage, the file starts here for the same reason as the next one.

`paste_system_alert.yaml` (BE-0369) covers the lane's cross-process paste. The proactive step answers it. `setClipboard` seeds the pasteboard from outside the app. The app's own read then raises iOS's paste-consent prompt. `handleSystemAlert: { prompt: paste }` answers that prompt. It uses a native accessibility query. The scenario covers granted and denied. Each runs under the target's locale and again under `locale: ja_JP`. The pair checks the label lookup against a SpringBoard in another language than the run's own. The scenario is new on-device coverage. It earns its stability here before any promotion into `run`.

**golden (xcuitest)** runs the BE-0006 element-tree golden over XCUITest (`golden.yaml`). The job is the iOS twin of `android-e2e.yml`'s `golden (adb)`.

**codegen (xcuitest)** generates a native XCUITest from a scenario (`make -C demos/showcase ui-test`). It runs the test with `xcodebuild`, with no bajutsu or AI at test time. It compiles the UITests Scheme, a different product than the resident runner. The job builds its own product and is not a `build`-artifact consumer.

**bundled-runner (xcuitest)** runs a smoke scenario against both the SwiftUI and UIKit a11y apps. The runner resolves from the wheel bundle rather than a config `testRunner` (BE-0292). That proves the runner is app-agnostic.

**conformance (xcuitest)** runs the driver conformance suite (BE-0114). It runs on-device against the XCUITest backend.

- The suite recovers from a Simulator infrastructure fault the way `bajutsu run` does. Such a fault is a resident-runner crash, `base.BackendCrashError`. The suite re-leases a fresh device and re-runs the affected test. Two limits bound each test. One is the retry count (`BAJUTSU_CRASH_RETRIES`). The other is the wall-clock respawn budget (`BAJUTSU_CRASH_RECOVERY_BUDGET`).
- This required check thus reddens on a real contract violation, not a host flake. A crash on every attempt still fails with an explicit error.
- The suite counts every respawn into the uploaded `conformance-recovery-report` artifact (BE-0334).
- A wedged CoreSimulator is the host's fault, but not a respawn's to repair. Rebuilding the device consists of the same `simctl` calls that stalled a moment ago. The suite names the wedge a host fault in the job log. It counts the wedge into the same artifact instead of retrying.
- The suite resolves the app's data container once per lease rather than once per test. That one read gets a second attempt when the first exceeds its deadline.
- A stall that the second attempt absorbs reddens nothing. The suite counts it in the artifact as its own leading indicator. The host that wedges the check later is this one getting worse (BE-0378).

**fault-injection (xcuitest)** signals the runner's own host process (BE-0305). It does not drive a healthy runner:

- A brief `SIGSTOP` hangs the channel the way a wedged runner does. The transient retry (BE-0207) must absorb the hang.
- Crash recovery (BE-0287) must ride out a freeze past the retry budget.
- A `SIGKILL` must end the read on a crash diagnosis. The diagnosis must name a mid-run runner fault, not an unrelated timeout.

Each case lifts its fault on the channel's own log record that it reached the layer under test. No case relies on a guessed delay.

The job landed as a per-PR signal. A runner-HTTP fix had been reddening it. Once that fix landed, the job joined `E2E (iOS)`'s `needs:`, with 64 runs without a failure since.

**visual (xcuitest)** pixel-compares the Stable catalog against the committed `baselines_ios/` baseline. The command is `make -C demos/showcase e2e-visual`. It masks the status bar and the "Liquid Glass" tab bar. Its `e2e-visual` rebuilds the app itself. Like `codegen`, the job is not a `build`-artifact consumer.

**network (xcuitest)** drives the real network path over the iOS transport (BE-0282). The command is `make -C demos/showcase e2e-network`. The job is the iOS twin of `network (playwright)` in `web-e2e.yml`. The same holds for `network (adb)` in `android-e2e.yml`.

- BajutsuKit's in-app `URLProtocol` answers a mocked `POST /post` with a 201 status. A live server would answer with a 200 status. The protocol reports each exchange to the collector on loopback.
- A second scenario issues an unstubbed catalog `GET`. It asserts that the job observed the exchange, and nothing more.
- `demos/showcase/network/assert_network_evidence.py` asserts the persisted `network.json`. The mocked exchange's `Authorization` header and `password` body field must show masked values. No raw secret may appear anywhere in the file. The unstubbed exchange must carry `mocked` false. An over-broad mock matcher thus cannot claim traffic that nothing stubbed.

The masking check is the gap BE-0282 exists for. Every pure redaction test feeds the algorithm a hand-built exchange. Nothing else on iOS observes a credential captured for real. This check alone sees whether shipped evidence masks it. The job drives a `make` target that builds what it needs. Like `codegen` and `visual`, the job is not a `build`-artifact consumer.

The lane carried a two-device **pool (xcuitest)** job (BE-0298) until the job was withdrawn. The job booted a second Simulator. It asserted the device pool's isolation claim across one `bajutsu run --workers 2`. It returned that verdict once in five runs.

The other four runs collapsed on the host rather than on any pool check:

- `SimRenderServer` crashing on its own dispatch queue
- a wedged CoreSimulator timing out `simctl uninstall`
- the runner channel becoming unreachable mid-run

Each red run spent about half an hour of a runner billed at 10x. Cutting the video, the touch markers, and half the scenarios moved the collapse earlier. It did not remove the collapse. `pool (adb)` on the Android lane now checks the claim against real concurrent devices alone. On the iOS lane, the fast suite's bookkeeping proof (`tests/runner/test_pool.py`) alone checks it.

#### iOS merge gating

The same `changes` detector path-gates all nine jobs. Six jobs also feed a single always-reporting `E2E (iOS)` job, the required check:

- `build`
- `run`
- `codegen`
- `bundled-runner`
- `conformance`
- `fault-injection`

`actuation`, `golden`, `visual`, and `network` run under the same path gate. The `needs:` of `E2E (iOS)` leaves them out on purpose:

- Newly wired XCUITest actuation lands as a per-PR signal first. The Simulator lane has a flakiness history (BE-0218). The actuation job reaches the gate once stable.
- The element-tree `golden` is deterministic and host-independent. Its drift surfaces as a per-PR signal.
- A `visual` pixel baseline is host-specific. The Simulator renderer varies by Xcode, device, and OS.
- `fault-injection` breaks the runner on purpose. It landed as a signal first on the same BE-0282 path.
- `network` is itself newly wired on-device coverage. It lands as a signal first too, the path its own web twin took before joining `E2E (web)`.

Each of these jobs surfaces a drift or a flake as a signal on its own job. None blocks a merge. The `visual` job uploads its captured screenshot as `ios-e2e-visual-run`. The screenshot serves to re-record the baseline.

### `android-e2e.yml`: the Android (adb) backend lane

This workflow is the **Android (adb) backend** lane (BE-0208). It runs eight per-concern jobs that mirror the iOS and web lanes' job splits. Each job boots its own x86_64 API 34 Android Virtual Device (AVD) under KVM. It uses `reactivecircus/android-emulator-runner`. A ninth job, **warm gradle cache (adb)**, has no AVD.

The lane needs **no Mac / Simulator**. The lane is the third backend's Linux twin of the iOS and web e2e lanes. A `changes` detector path-gates it (`scripts/e2e_changes.py`, `E2E_LANE=android`). The detector feeds the required `E2E (android)` aggregator (BE-0279).

#### Android Gradle warm-up

**warm gradle cache (adb)** builds one set of APKs up front. The set is the Compose + resident-server APKs. `smoke`/`golden`/`conformance`/`fault-injection`/`pool` all build them. Those five jobs' own unchanged builds then restore a warm Gradle dependency and build cache. They no longer resolve and compile cold five times over.

- The job skips Views. `smoke` alone needs Views. Warming Views here would make the other four jobs wait on a compile none of them uses. The wait comes from a job-level `needs:`. `smoke`'s own Views build stays cold, the same as before this job existed.
- `actions/cache` cannot share a still-unsaved cache between concurrent jobs of one run. The warm-up runs first, and those five jobs declare `needs: warm-gradle-cache`. None of them passes `cache-read-only` explicitly. They rely on the composite action's own default. That default is safe: `true`, the mode that reads the cache without writing it. `gradle/actions` documents this multi-job cache-sharing pattern.
- `network`, `visual`, and `codegen` stay out of that `needs:` on purpose. `codegen` also needs the Compose AndroidTest/instrumentation variant. warm-gradle-cache never builds that variant. A job-level `needs:` would also block their own unrelated AVD-cache step until the build finished. That costs those lighter jobs more than a cache hit could save.
- A miss or an outright failure in the warm-up costs time and never correctness. Every consumer job's `if:` thus uses `!cancelled()`. It does not inherit a hard `needs` dependency on the warm-up's success. It does not use `always()`. That function would survive this file's own `concurrency: cancel-in-progress`. A superseded run's jobs would keep dispatching. The warm-up never joins `E2E (android)`'s `needs:`.

#### Android jobs

- **smoke (adb)** builds the Compose and Views showcase APKs. It runs the Stable-tab scenarios through `--backend android` (`make -C demos/showcase/android e2e`). The scenarios are the core id/tap/type/value flows plus a push/pop back-navigation flow.
- **golden (adb)** runs the on-device golden element-tree check (BE-0006 / BE-0208 unit 4). The catalog is the Compose Stable catalog. The command is `make -C demos/showcase/android e2e-golden`. The job then re-runs the check with the resident channel forced off (BE-0245). That command is `make -C demos/showcase/android e2e-fallback`. The two reruns prove that both read channels agree.
- **network (adb)** drives the BE-0283 network-capture assertion. A `request` step observes real emulator traffic. BajutsuAndroid's interceptor reports that traffic over the `adb reverse` collector bridge.
- **conformance (adb)** runs the driver conformance suite (BE-0114) against the real adb backend. The job is the Android twin of `ios-e2e.yml`'s `conformance (xcuitest)`.
- **fault-injection (adb)** puts the display to sleep (BE-0305). The command is `make -C demos/showcase/android e2e-fault-injection`. The real read source then serves a genuinely empty accessibility tree. The job checks two points. First, `CoordinateTreeDriver`'s transient-empty retry rides over the real condition. Its fast-suite test cannot do more than fabricate that condition. Second, an empty tree that outlives the retry budget fails with an explicit error. The job now feeds the required check, described below.
- **visual (adb)** runs the pixel visual-regression test (VRT) described below.
- **pool (adb)** is the lane's sole two-emulator job (BE-0298), described below.
- **uiautomator (codegen)** is the codegen output path, described below.

**pool (adb)** boots a second instance of its cached AVD with `-read-only`. It does so from inside the emulator-runner's own step (`scripts/android_pool_e2e.sh`). The script exists because that action has no two-emulator mode. Its emulator lives for that step's length. The job runs four state-neutral scenarios through one `bajutsu run --workers 2`. The command is `make -C demos/showcase/android e2e-pool`. It asserts the same isolation claim with `scripts/assert_pool_isolation.py`.

- Both instances run at `-memory 3072 -cores 1` and with `-read-only`. The flag lets two emulator processes share one AVD.
- The job's own emulator carries the flag too. An instance holding the AVD read-write locks it and refuses the second outright.
- `-read-only` also disables snapshot load. This is the one job on the lane that always cold-boots both devices. Its own AVD cache entry buys the AVD's creation rather than a resume.
- The cache key uses the narrower `pool` output rather than the lane-wide one.

The `visual (adb)` job runs a pixel visual-regression check (BE-0208 unit 4). The catalog is the Compose Stable catalog. The command is `make -C demos/showcase/android e2e-visual`. Unlike the element-tree golden, a pixel baseline is host-specific. The x86_64 software renderer (swiftshader) and a local arm64 emulator diverge per pixel. The baseline thus comes from this x86_64 lane, not from arm64. The baseline lives under `demos/showcase/scenarios/visual/baselines_android/`. The job masks the top status bar so the wall clock never churns the comparison.

The same target then runs `status_bar_android.yaml` (BE-0478 unit 6). It runs `background` and `foreground` over the Home key. It then runs `overrideStatusBar` through the System UI demo mode. The job compares the screenshot's status-bar strip alone against its own committed baseline. It also reads the per-app locale `ja_JP` back from the app's own configuration.

The `uiautomator (codegen)` job is the codegen output path (BE-0294). The command is `make -C demos/showcase/android e2e-codegen`. The job is the Android twin of `ios-e2e.yml`'s `codegen (xcuitest)`. It re-generates a native UI Automator (Kotlin) test from `codegen_android.yaml`. Gradle's `connectedAndroidTest` then builds the Compose a11y app and the instrumentation APKs. It installs both and runs the generated test against the emulator. No bajutsu, adb driver, or AI runs at test time. Regenerating before the build means a stale check-in cannot mask emitter drift. It cannot mask `androidx.test.uiautomator` API drift either.

#### Android emulator and scenario coverage

- The AVD is x86_64, not the local validation's arm64. KVM then accelerates it on the x86_64 runner. The golden's baseline comes from arm64 yet passes on x86_64. The comparison is field-level with a tolerant frame check.
- The sheet/cover flows (`components`, `modals`) run on this lane. The lane raises its condition-wait ceiling to allow it. `make -C demos/showcase/android e2e` exports `BAJUTSU_MIN_WAIT_TIMEOUT` (default 15s). It sets a floor under each wait's own timeout. The software-rendered emulator draws a modal slower than the shared scenarios' 5s waits allow. A condition wait returns the instant its condition holds. The larger ceiling is a safe upper bound, not a fixed delay. The shared scenarios stay untouched (their `timeout: 5` is the same on every backend).
- The deep-scroll flows (`controls`, `notices`) join the lane too. Both reveal a far target with `scroll` (BE-0326). `controls` reveals the segmented-control value node below the buttons. `notices` reveals a list row well below the fold. `scroll` is a bounded, non-inertial step. It re-queries the tree and stops the moment the target is on-screen. A swipe chain, by contrast, follows a fixed distance. A fixed-distance chain covers far less of Android's dense screen (2400px) than of iOS's (~900pt). `scroll`'s re-query removes the need to retune the distance per backend (BE-0208 unit 5). `system` and `modals` reveal their far targets the same way as of this change. Those targets are `sys.paste.value` on the Permissions tab and `log.dialog.value` on the Log tab. Four of the lane's flows now rest on `scroll` rather than a tuned swipe chain.
- The single-touch gesture flow (`gestures`) joins too. On a rooted emulator, the adb driver drives a double-tap with a raw `sendevent` touch sequence. The `e2e` target runs `adb root` first. The sequence fires both taps inside the platform double-tap window. A per-tap `input` JVM overran that window. On a non-rooted device the driver falls back to `input tap` unchanged. The flow's reveal swipes rest on unit 5's other half. The default directional swipe travels a screen *fraction*. The constant is `_SWIPE_FRACTION` in `bajutsu/common/orchestrator/actions/handlers/gestures.py`. It covers the same proportion of Android's dense screen (2400px) as of iOS's (~900pt). A fixed coordinate count did not (BE-0208 unit 5).
- The multi-touch gesture flow (`gestures_multitouch`) joins too (BE-0232). On the rooted emulator, the adb driver drives a pinch / rotate as a raw two-slot `sendevent` sweep. Two contacts move together across interleaved frames. The shared scenario that iOS runs on XCUITest thus runs unchanged on Android. Nothing can approximate a two-finger gesture. Unlike the single-touch double-tap, there is no `input` fallback. The flow requires root and fails with an explicit error otherwise.
- The runtime-permission flow (`permission`) joins the lane too (BE-0208 unit 6). It exercises BE-0210's up-front grant. The flow uses the **same** `permission.yaml` the iOS lane runs. The grant mechanism lives in config, not the scenario, so one file serves both. `showcase-compose` grants `POST_NOTIFICATIONS` up front (`grantPermissions` → `pm grant` at lease time). Android's `RequestPermission` contract then short-circuits to granted with no dialog. The scenario's `systemAlertHandling` guard never fires here. That keeps the flow deterministic (no LLM, no fixed sleep) on the lane. On iOS, notifications cannot be pre-granted, so that guard taps "Allow" instead.
- The device-control flow (`device`) joins the lane too (BE-0208 unit 5). It overrides the GPS location (`emu geo fix`) and round-trips the clipboard. It then re-asserts the settled screen. The flow uses the **same** `device.yaml` the iOS lane runs. Both platforms advertise `setLocation` and the clipboard. That unifies the file across iOS and Android. The `push` half lives in `push.yaml`. The flow runs on the Stable launch tab. The smoke job also runs `push.yaml` and `push_android.yaml`. They deliver a push through BajutsuAndroid's push receiver. They assert the title that the app's handler shows (BE-0498). It exercises both `setLocation` and `clipboard` of the device-control family. `cmd clipboard` is a silent no-op on-device. Since Android 10, the foreground app alone may touch the clipboard. The clipboard thus runs through an in-app receiver (BE-0233). The showcase embeds the receiver from `BajutsuAndroid`. The seed/read-back is the strong assertion PR #934 wanted.
- The location flow (`location_android`) joins the lane as well (BE-0481). `e2e` runs it over the emulator's own serial, which takes `emu geo fix`. A separate **location over adb connect (adb)** job reruns it through `e2e-location-connect`. The rerun uses the same kind of emulator, reached with `adb connect`. That job is a per-PR signal until it has a run history. Until then it stays outside `E2E (android)`'s `needs:`, as `golden` and `app-crash` did. A `<host>:<port>` serial has no console that adb can reach. Thus `setLocation` goes through BajutsuAndroid's in-app location receiver. A real device takes the same route.
- The interrupt-handler flow (`interrupts`) joins the lane too (BE-0314). The flow is a syntax demo. Its `interrupts` entry never actually fires against the real app. The flow underneath is the same stable→horse→favorite one `firstlook` already runs. What it adds over `firstlook` is BE-0314's *check* path against a live tree. A `wait` rides its own poll tree at no extra cost. A bare act step pays one extra query for the guard. This scenario declares no `screenChanged` policy. A never-matching handler is thus proven on a real device to disturb nothing. It costs one bounded read per act. No fast-suite fake can show that. The scenario's ids carry both dotted SPEC and underscore Android Views forms (BE-0221). It runs unmodified.

#### Android merge gating

Four jobs are deterministic and host-independent. They feed the always-reporting `E2E (android)` aggregator, the required check (BE-0279):

- `smoke (adb)`
- `conformance (adb)`
- `network (adb)`
- `fault-injection (adb)`

The aggregator's `needs:` leaves out four jobs on purpose. They are `golden (adb)`, `visual (adb)`, `uiautomator (codegen)`, and `pool (adb)`. They stay per-PR signals:

- The element-tree golden can drift with an upstream dependency.
- A pixel baseline is host-specific.
- The codegen and two-emulator lanes land as signals first, per the BE-0282 precedent.

This is the same gate boundary that iOS's `E2E (iOS)` draws.

`fault-injection (adb)` walked that same path to its end. It had 73 runs for one failure. The emulator never came up, which is not anything the lane asserts. `conformance (adb)` had four failures in 67 runs over the same span. That record moved `fault-injection (adb)` out of signal status and into the aggregator's `needs:`. A regression in the retry's real detection now blocks a merge. Before, it surfaced as a red that a merge could ignore (BE-0305).

`tests/test_e2e_gate_needs.py` pins three properties on the fast suite:

- each aggregator's composition
- that the aggregator reads the result of every job a gate `needs:`. A failed dependency must not pass as the path-skip, which the aggregators count as green
- that each of those results actually reddens the check, by running the verdict script itself

### `devicefarm.yml`: the AWS Device Farm batch submit

This workflow is the **AWS Device Farm batch submit** (BE-0235). It builds the showcase Compose APK. It packages Bajutsu, the config, and the scenarios. It then hands the package to [`scripts/devicefarm_submit.py`](../scripts/devicefarm_submit.py). The script does the following:

- It uploads a custom-environment test spec that runs `bajutsu run --backend adb` on Device Farm's host.
- It polls the run.
- It downloads the artifacts.
- It surfaces **Bajutsu's own manifest verdict**, never Device Farm's classification.

The workflow is CI-side glue outside the deterministic core, so no LLM touches the verdict. It runs on `workflow_dispatch` alone, never on push/PR, and is not a required check.

Authentication is a short-lived AWS credential, `AWS_DEVICEFARM_ROLE_ARN`. It comes from GitHub OpenID Connect (OIDC). A `devicefarm` Environment scopes the credential. The project and device-pool ARNs live in repository variables. With any of them unset, the job is a green no-op. It stays dormant until an operator wires up an account.

The real-account serial-resolution proof of concept (PoC) is a documented human procedure. See [AWS Device Farm](devicefarm.md). The procedure stays off the deterministic gate on purpose.

### `ai-smoke.yml`: the live-model lane

This workflow is the **live-model** lane. It has two jobs. Both are **non-gating** signals (the BE-0282 precedent).

**smoke (direct Anthropic API)** (BE-0300) calls a real provider. The call goes through Bajutsu's own adapter code, `bajutsu.ai.anthropic.AnthropicBackend`. It sends a trivial forced-tool prompt. It asserts that the reply lands as a populated, parseable neutral `MessageResponse`. The command is `pytest tests/test_ai_backend_live_smoke.py -m live -n0`. The assertion covers nothing beyond that. Every other adapter test drives a hand-written `FakeAnthropic`. Nothing else re-observes the live API's actual shape. The job is a transport-and-schema check, never a model-quality one.

**accuracy (system-alert guard)** (BE-0308) asks the other live question. It asks whether the answer is right, not whether the transport round-trips. The job shows real iOS system dialogs to the vision alert locator. The locator is `bajutsu.agents.alerts.ClaudeAlertLocator`. The showcase app supplied the dialogs. The assertion is that the locator's coordinates land inside the right dismiss control's frame. The command is `pytest tests/test_real_model_alerts.py -m live -n0`. That claim is the guard's own safety claim. Every other test of the guard takes the claim on faith. Those tests hand the guard an `AlertDecision` that its author typed.

The dialogs are captures stored under `tests/fixtures/be0308/`:

- a notification prompt
- a location prompt
- iOS's cross-process paste consent
- the showcase app's own delete confirmation. The guard must not reach for its destructive button at all

The captures need no Simulator, so the model call is the one live part. A deterministic check that each capture is self-consistent runs on every gate without a credential.

Neither job puts an LLM on the `run` / CI verdict (prime directive 1). Both exercise the AI authoring periphery alone. The `live` marker keeps both suites out of the fast gate (pyproject `addopts` `not live`).

Both jobs run on `workflow_dispatch` alone, never on push/PR. A fork-triggered run thus can never see the credential (the boundary `devicefarm.yml` draws). The credential is the `ANTHROPIC_API_KEY` repository secret, scoped to the `ai-smoke` Environment. With the secret unset, every test skips itself and both jobs are green no-ops. The lane stays dormant until an operator wires the secret up.

The lane wires the direct Anthropic API adapter alone. Bedrock needs a live AWS role. `ant` needs a signed-in OAuth CLI seat. Neither is realistically a CI secret. Their `-m live` tests still run locally or manually.

### Which E2E checks gate a merge (BE-0279)

Each backend lane — iOS (`E2E (iOS)`), Android (`E2E (android)`), and web (`E2E (web)`) — carries one
always-reporting aggregator that is the lane's required status check; per-backend aggregators keep
attribution, so a red check names the backend that broke. **A check gates a merge if, and only if, it
is deterministic and host-independent.** A check whose result depends on the host or on an upstream
dependency stays a non-required signal — it still runs and surfaces a drift on its own job, but it
never blocks a merge:

- **Pixel visual-regression (VRT)** — the `visual` jobs. A pixel baseline is host-specific (a
  Simulator or emulator renderer varies by OS, device, and toolchain), so a drift is unrelated to any
  Bajutsu change. Excluded from the aggregators' `needs:`.
- **Element-tree golden** — the iOS/Android `golden` jobs. Deterministic, but on Android the tree is
  read through an upstream on-device server whose drift could redden it independently of any Bajutsu
  change, so a golden drift is best surfaced as a per-PR signal rather than a merge blocker.
  Excluded from the aggregators' `needs:`.

Because a required check skipped by a `paths:` filter stays pending forever and blocks the merge, none
of the lanes path-gate at the trigger. Each triggers on every PR (and the merge queue) and a `changes`
job path-gates the heavy jobs instead, running [`scripts/e2e_changes.py`](../scripts/e2e_changes.py)
with `E2E_LANE=ios|android|web` (the per-lane relevance filter, unit-tested in
`tests/test_e2e_changes.py`). The aggregator runs `if: always()`, so a path-skip reports as a pass and
an unrelated PR is neither run nor blocked. Adding a new required aggregator to `main`'s
branch-protection ruleset is an out-of-repo administrative step, done by a maintainer with the exact
check name.

The filter inverts its default for `bajutsu/` (BE-0333). Rather than a hand-kept positive list that a
file must join to be seen — where anything unlisted silently fired nothing — the shared core sweeps
the *whole* package and carves out only what is explicitly classified: the periphery no lane exercises
(`_PERIPHERY_EXCLUSIONS`, each entry carrying the reason it is out — the analytics / analysis stacks,
the MCP server, the AI adapters, the GitHub and cloud integrations, and the individual periphery
modules of the mixed `agents` / `crawl` / `cli.commands` packages) and the per-backend leaves each
lane reclaims (`_LANE_CLAIMED`). A file named in neither is swept in and fires all three lanes until
somebody classifies it — a wasted job, the safe direction — instead of the silent under-trigger the
positive list produced. That default retires a whole class of miss: a top-level module split into a
package (`bajutsu/config`, `bajutsu/platform_lifecycle` both drifted that way, skipping the fleet the
lane exists to exercise), a new top-level module or CLI command, and files the run path imports but
the list never named — `bajutsu/common/report/`, the manifest writer every run invokes, chief among them —
now all fire because a package and a module match the sweep alike.

Several tests keep the classification honest. `test_run_path_closure_is_gated_or_excluded` walks the
run path's static `ast` import closure and fails if any file it reaches is neither gated by a lane nor
a classified periphery entry — so a module the run path *starts* importing that nobody classified
fails `make check` rather than surfacing months later as a mysteriously green required check.
`test_periphery_exclusion_paths_exist` and `test_every_plain_literal_path_in_the_filter_exists` resolve
every exclusion entry and every plain path against the tree, so a rename or a deletion fails the gate
instead of leaving a pattern matching nothing. And the shared core sweeps the two per-backend
directories (`bajutsu/common/drivers/`, `bajutsu/platform_lifecycle/environments/`) minus the leaves each lane
names, with a test asserting no file under either fires zero lanes and another asserting each
`_LANE_CLAIMED` leaf is reclaimed by at least one lane — so a newly added backend module over-fires
rather than going unseen, and a leaf carved out but claimed by nobody fails the gate. The inverted
default's over-fire cost was measured before it shipped: across the last 80 merged pull requests it
fired identically to the old positive list on all three lanes (`scripts/e2e_overfire_report.py`).

The `changes` job narrows one step further for the one case it can prove safe (BE-0322): a change
confined to a lane's scenario files fires only the jobs that declare a changed scenario, rather than
the whole lane. The filter reads the `scenarios:` each iOS job declares in the workflow — and, for
the `codegen` and `visual` jobs, the scenarios their `demos/showcase/Makefile` targets run (BE-0338),
since those two name their scenarios in a Makefile target rather than a workflow input — so the map
from a changed scenario to the jobs that load it never drifts from what those jobs run, and emits —
alongside `relevant` — a `shared` flag and an `affected` job array the jobs guard on. A test pins the
Makefile-read attribution against those targets, so a target gaining or losing a scenario fails the
gate unless the map moves with it. Every other case still fires the whole lane: a shared-code change
(driver / runner / app / workflow code that can affect any scenario), the `conformance` dimension job
that declares no scenario subset (it drives the whole driver-conformance harness), and a lane whose
jobs are not scenario-keyed at all (Android and web). The decision over-selects toward the whole
lane, so it never skips a job a change could have broken, and — reading only the `git` diff, the
workflow's own declarations, and those Makefile targets — puts no LLM on the path and has no bearing
on any run's pass/fail verdict.

The dev tools live in the `dev` dependency group, so the Linux job runs `uv sync --group
dev` then `uv run --no-sync …` (plain `uv run` would re-sync to the default set and drop
them). The gate mirrors [`make check`](../Makefile) and the [`pre-push`](../.githooks/pre-push)
hook step-for-step; every check except `actionlint` and `gitleaks` (two tools CI installs
separately, each step skipping with a notice when its tool is absent) runs identically on a fresh
clone via `uv` alone, which is what makes "green locally" predict "green in CI". The skill-drift
check is in the `uv` half, not the exceptions: `apm-cli` is a `dev` dependency (BE-0390), so
`make lint-skills` has no skip branch.

### Every job declares a timeout

Every job under [`.github/workflows/`](../.github/workflows/) sets `timeout-minutes`. When a job
declares none, GitHub's own default waits 360 minutes before cancelling it, so a stalled job holds a
runner and spends Actions minutes for most of a day before a red check appears. The web lane has
already paid that cost: GitHub cancelled `conformance (playwright)` and `codegen (playwright)` at
almost four hours apiece, against the two to seven minutes each one takes when it passes.

A job whose bound follows from its own arithmetic sets its own value and gives the reasoning in a
comment beside it: 180 minutes for the AWS Device Farm submission, 60 for the iOS scenario jobs, 30
for the Android `smoke` job and 25 for the other Android scenario jobs, 20 for `claude-review`, 15
for the Android Gradle-cache warm-up and for each of `ai-smoke`'s two live-model jobs, and 10 for
`mcp-wire`. Every job carrying no such reasoning takes the repository default
of **30 minutes**. That default clears the slowest successful job we have measured, CodeQL's Swift
analysis at about 20 minutes, and still turns a stall into a red check inside the half hour.

Two jobs carry no `timeout-minutes` of their own: the ones in
[`docs-refresh.yml`](../.github/workflows/docs-refresh.yml) and
[`roadmap-refresh.yml`](../.github/workflows/roadmap-refresh.yml) that call the reusable
[`refresh.yml`](../.github/workflows/refresh.yml) workflow, where GitHub does not accept the key at
all. The 30-minute bound on `refresh.yml`'s own job covers both callers instead.

### What a failing E2E job collects (BE-0361, BE-0367)

The hardest E2E failures are the ones where nothing crashes. On iOS the resident XCUITest runner —
the XCTest host `bajutsu` spawns with `xcodebuild test-without-building` and drives over a loopback
Hypertext Transfer Protocol (HTTP) channel — reports `Timed out while requesting screenshot`, ends
its test method, and leaves the Python side to declare a mid-run crash. On Android the resident UI
Automator server — the on-device process the adb backend reads the element tree from — stops
answering, or the emulator's own renderer wedges, or the job runs out its `timeout-minutes`. No
process died in any of those, so a crash-report sweep finds nothing, and the state that would say
whether the device's render service wedged or the host starved it disappears before anyone opens the
job.

Both lanes answer that question the same way, in three layers split by what each one can see: inside
`bajutsu`, because the running process is the one thing that knows *when* a stall happens; from CI
about the device, which only something holding that device can read; and over time about the host,
which only a sampler running alongside the job can record. Every artifact lands under
`runs/diagnostics/`, which the jobs already upload — with one exception, the per-scenario crash
evidence below, which lands in the failing scenario's own evidence directory instead, because it
belongs to that one scenario rather than to the job. None of it reaches a verdict: the collection
writes files and nothing else, and the deterministic assertions still decide pass/fail.

One environment variable arms the first layer on both backends. `BAJUTSU_STALL_DIAGNOSTICS` names
the directory captures are written into and deliberately carries no backend prefix, so a single
operator setting turns the hook on for whichever backend a job drives. What a capture *reads* is
per backend, because the two share no command — a Simulator answers to `simctl` and a macOS host to
`vm_stat`, an emulator answers to `adb` and a Linux host to `top` — so each backend contributes its
own set of probes to one shared capture, which owns everything else: the opt-in gate, a wall-clock
budget per capture, a budget of two captures per trigger, and the summary each probe's outcome is
written to. The budget is per *trigger* so that one frequent trigger cannot
spend a shared budget before the crash the capture exists to explain ever arrives. The video trigger
was that frequent one: it fired on every scenario of these runners, green runs included, because the
confirmation it fired from watched the wrong signal. It no longer does, but a crash-looping run
still has more than one trigger competing, so the split stays. Unset — which it is everywhere but these two
lanes — the hook does nothing at all.

#### The iOS lane

Every iOS job that boots a Simulator except `codegen` collects the three layers into `runs/`;
`codegen`'s sole artifact is its `.xcresult`, so the collection has no `runs/` directory to ride
there.

- **Inside `bajutsu`.** `BAJUTSU_XCUITEST_RESULT_BUNDLES` gives every runner spawn a
  `-resultBundlePath`, so `runs/runner-logs/result-<udid>-<port>.xcresult` records what testmanagerd
  itself saw — the precise XCTest failure and its timestamps — rather than the paraphrase the
  captured stdout carries. The same argv pins `-collect-test-diagnostics never`: left at its
  `on-failure` default, `xcodebuild` embeds a Simulator `system.logarchive` in the bundle, measured
  at 163 MB for a single spawn, which is the whole-archive collection the targeted extracts below
  exist to replace. The stall capture fires the moment the channel declares a mid-run crash or a
  `recordVideo` produces no bytes, writing a timed `simctl` screenshot, `sample` output for the
  rendering processes, and a `ps` / `vm_stat` snapshot into
  `runs/diagnostics/stalls/stall-NN-<reason>-<pid>/`.
- **Inside `bajutsu`, per failing scenario, with nothing to opt into** (BE-0421). A scenario whose
  crash-recovery retries all exhausted gets a `crash-diagnostics/` subdirectory in its *own*
  evidence directory, `runs/<run_id>/<sid>/`, holding the tail of the crashed runner's captured
  output — far more of it than the twenty lines the crash warning folds in, but still bounded — and,
  whenever macOS wrote one for the faulted `xcodebuild` process, its `.ips` crash report; the
  scenario's failure string names that directory, so nobody has to already know it exists. This is
  the local-run and per-scenario counterpart to the sweep below: that one tars up the whole job's
  report store, which cannot say which crash belongs to which of a dozen scenarios, and runs in CI
  only — a developer reproducing the crash with a local `bajutsu run` got nothing from it at all.
  Both halves are best-effort: a report `ReportCrash` never wrote, or a `DiagnosticReports`
  directory this host does not have, leaves that entry out rather than failing anything.
  A crash of the **app under test** is a separate directory with a separate cause: `app-crash/`,
  written for any scenario whose app went down mid-run, holding the platform's own report for it
  ([`docs/evidence.md`](evidence.md#app-crash-evidence-be-0424), BE-0424). The two never overlap —
  `crash-diagnostics/` names a fault in the test infrastructure, `app-crash/` a likely defect in the
  app being tested — and the app-crash path deliberately triggers no retry. Both iOS and Android
  lanes carry a non-gating `app-crash` job driving a showcase affordance that crashes on demand,
  next to `actuation (xcuitest)` and `golden (adb)`: newly wired on-device coverage earns its
  stability as a per-PR signal before any promotion into the required aggregators, the same path
  every other new signal in those lanes has taken. Back in `crash-diagnostics/`, the runner
  output is collected for the two crash shapes the runner's own liveness signals name — `xcodebuild`
  exited, or it lingers past a test run its capture says has ended. A channel that simply stopped
  answering while the process runs on and writes no such marker is the wedged-Simulator shape
  BE-0354 escalates instead, and this directory stays empty for it.
- **From CI, about the Simulator and CoreSimulator**, through the
  [`collect-ios-diagnostics`](../.github/actions/collect-ios-diagnostics/action.yml) composite action
  every Simulator-driving job calls. Its cheap tier runs on every job: the tail of
  `CoreSimulator.log`, the booted device's own CoreSimulator log directory, a crash-report sweep
  widened past `.ips` / `.crash` to the hang, spin, and jetsam reports this failure class actually
  leaves, and a host snapshot (`sw_vers`, `sysctl`, `xcodebuild -version`, `simctl list`) that keys
  each red run against a runner image and hardware generation. Its heavy tier runs when the job's own
  run step failed, and not otherwise: `xcrun simctl diagnose`, and two targeted unified-log extracts.
- **Over time, about the host**, from the same action's `start` phase: a background sampler appends
  `top`, `vm_stat`, and `memory_pressure` to `runs/diagnostics/host-telemetry.log` every 20 seconds,
  and a one-shot render probe records how long a screenshot takes and whether a five-second
  `recordVideo` produces any bytes at all. The sampler is an observer outside every run loop, so its
  interval is a sampling cadence and not a wait a verdict depends on. It ranks `top`'s rows by
  resident size rather than CPU, because `top -l 1` differences nothing and so reports every process
  at 0.0% — sorting by CPU there sorts on a constant, and the first collection's rows never once
  included the app under test.
- **Before anything ran**, also from `start`: one bounded `ps aux` plus `vm_stat` land in
  `runs/diagnostics/ps-baseline.txt`. The stall captures snapshot `ps` at a stall, which answers what
  was resident when it broke and cannot answer whether that differed from normal — the first
  collection had to settle that question from a launch-env argument instead of a measurement. Both
  this and the render probe are written once per job, so the second `start` a job may make never
  overwrites the pre-run reading with a mid-job one.
- **Live, about each synthesized touch**, also from `start` and kept on green jobs:
  [`scripts/synthesis_stream_record.py`](../scripts/synthesis_stream_record.py) streams the guest's
  `log stream` for the runner's `bajutsu.runner` markers and for `backboardd` and `testmanagerd`,
  and writes each event to `runs/diagnostics/synthesis-stream.ndjson` with the time it reached the
  host. `collect` stops the recorder. A stream that ends mid-job (a lane that reboots the device
  ends it) is restarted, and every session is bracketed by `meta` records, so an empty or cut-short
  recording surfaces as a warning in the report instead of reading as a quiet run. A `log show` extract taken after the job carries no arrival
  time, so it cannot say how long a marker took to reach the host, or whether a `backboardd` line
  ever arrived behind a runner line stamped later than it. Those are the two numbers
  [BE-0462](../roadmaps/BE-0462-ios-dropped-touch-retry/BE-0462-ios-dropped-touch-retry.md)'s
  dropped-touch monitor needs. Download the artifacts and run
  [`scripts/synthesis_stream_report.py`](../scripts/synthesis_stream_report.py) over them to get
  the arrival-lag percentiles, the arrival-order inversions, and every drop line attributed to the
  touch window that contains it. The recorder is an observer and decides nothing.

We split the unified-log extract in two on purpose, and the reason is worth stating because the
opposite is the natural assumption. The Simulator's guest processes that serve screenshots —
`SpringBoard`, `backboardd`, and `testmanagerd` — do **not** write to the host's unified log.
Measured on a booted device, a host-side `log show` filtered on those process names returns its
header and nothing else. The guest's entries live in the device's own log store, so the action runs a
second `log show` inside the guest through `simctl spawn`, and keeps the host-side extract for the
CoreSimulator service processes that genuinely do log there. A single host-side extract would have
produced an empty file — the same empty-by-construction artifact this collection replaced.

#### The Android lane

Every job that boots an Android Virtual Device (AVD) collects the same three layers, `uiautomator
(codegen)` included. That job is where the collection matters most, not least: every path it uploads
is produced by Gradle *running the generated test*, so a failure before that point — a wedged AVD
boot, a codegen or build error, the job's own `timeout-minutes` — left `if-no-files-found: ignore`
to drop the artifact silently, and a job that failed outside its own test uploaded nothing at all.
Unlike the six `bajutsu run` jobs it runs no adb driver at test time, so the resident-read trigger
cannot fire there; its Layer 1 hook is the host-side recorder
([`screenrecord.py`](../demos/showcase/android/screenrecord.py)) confirming its recording is
producing bytes, which is what a wedged renderer — this lane's known flake — fails to do.

- **Inside `bajutsu`.** The stall capture fires at two moments, both narrow on purpose. The first is
  the resident channel's hierarchy-read fallback, where the driver gives up on the channel and
  degrades to the `uiautomator dump` subprocess for the rest of the lease — the point at which the
  read channel is gone rather than momentarily noisy. The second is a check that the device-side
  `screenrecord` is producing bytes rather than merely running, which separates a wedged renderer
  from a recording that never started. Either moment writes a host `ps` and `top` snapshot,
  `dumpsys SurfaceFlinger --latency`, and a `logcat` tail into
  `runs/diagnostics/stalls/stall-NN-<reason>-<pid>/`. The actuation path is excluded deliberately:
  two of the resident channel's own errors fire during perfectly healthy runs — an older server that
  has no actuation endpoint, and a lost response the driver treats as having landed rather than
  actuating twice — so capturing there would spend the per-trigger budget before a genuine stall
  could use it.
- **From CI, about the emulator**, through
  [`scripts/collect_android_diagnostics.sh`](../scripts/collect_android_diagnostics.sh). This half is
  a script rather than a composite action, and the constraint behind that is the sharpest way the
  Android lane diverges from the iOS one. Every device-touching step in the lane runs *inside* a
  `reactivecircus/android-emulator-runner` step, which boots the AVD, runs its `script:`, and kills
  it when that step ends. An ordinary step placed after it — and a step-level `if: failure()` gate —
  would therefore run with no device attached and collect nothing at all. So the sweep is invoked
  from the tail of each job's own `script:` and keyed off the run command's own exit code, the same
  shape the lane's `poll_cpuinfo` poller already uses to survive a failing run. Its cheap tier runs
  on every job: a full-buffer `logcat -d` dump — which, unlike the per-scenario `deviceLog` interval
  evidence, reads the device's own retained ring buffer and so still has something to show for a job
  that failed before any scenario's capture began — plus `dumpsys meminfo`, `getprop`, and
  `adb devices -l` for the environment snapshot that keys a red run against an API level and ABI.
  Its heavy tier runs when the run itself failed, and not otherwise: `adb bugreport`, Android's own
  comprehensive collector, and a rooted pull of `/data/tombstones` (native crash reports) and
  `/data/anr/` (Application Not Responding (ANR) traces) — the two crash-report classes the iOS
  sweep gets for free from the host's own diagnostic reports, but which live device-side here.
  The sweep bounds itself as well as each command, for the same reason the in-process capture
  carries a budget above its per-probe timeouts: on the wedged-device failure this exists to
  document, every `adb` read hangs to its full ceiling, so per-command ceilings alone would let the
  collection spend most of a job's `timeout-minutes` and turn a reportable failure into a cancelled
  job holding a truncated collection. A read the deadline cuts off says so in its own file, since
  "the budget ran out" and "this read found nothing" are different answers about the device.
- **Over time, about the host**, from the
  [`collect-android-diagnostics`](../.github/actions/collect-android-diagnostics/action.yml)
  composite action, whose `start` phase before each job's emulator step launches a background
  sampler appending `top` and `free` output to `runs/diagnostics/host-telemetry.log` about every 20
  seconds, and whose `collect` phase after it stops the sampler. Reading only the Linux runner is
  what lets this layer be a step at all, and bracketing the emulator step from outside is what lets
  it cover a job whose emulator step died outright.

The host sampler coexists with the lane's existing `poll_cpuinfo` input rather than replacing it,
because the two measure different things from different sides: `poll_cpuinfo` reads the guest's view
over adb, the sampler reads the host's own load. That difference is also why the sampler is always on
where `poll_cpuinfo` is opt-in — a host-side sample every 20 seconds issues no adb traffic and
samples a tenth as often, so it lacks the observer effect that keeps `poll_cpuinfo` off by default.

The two lanes keep separate composite actions on purpose. `collect-ios-diagnostics` reads `simctl`,
CoreSimulator, and the macOS unified log; `collect-android-diagnostics` reads the Linux host. The
underlying tools share no surface, so a single action would be a branch on the backend rather than
shared logic. What the two do share is the design — the cheap/heavy split, the `start` / `collect`
telemetry phases — and, inside `bajutsu`, the capture itself.

**Reading what a red lane collected.** None of this appears in `gh run view --log-failed`: it is
uploaded inside the failing job's own `runs/` artifact, so a session diagnosing from the log alone
never sees it. The [`investigate-ci-failure`](../.apm/skills/investigate-ci-failure/SKILL.md) agent
skill downloads that artifact and matches what it holds against the symptoms recorded in
[`known-ci-failure-patterns.md`](../.apm/skills/investigate-ci-failure/references/known-ci-failure-patterns.md),
classifying the failure as a known infrastructure flake, a real defect, or something still
unclassified. [`pr-followup`](../.apm/skills/pr-followup/SKILL.md) runs it before touching a fix, so
a host flake is not answered with a code change and a regression is not waved through as a flake.
The skill reports; it never edits, pushes, or re-runs.

### Measuring a flaky lane across runs

The collection above explains one red job. It cannot show which factor recurring red jobs share.
Each piece is free text in one job's artifact. A per-job telemetry record closes that gap. It lines
the evidence up across runs, so a reader can compare red jobs with green ones.

**What every job writes.** Each lane's diagnostics `collect` phase ends with one more step. It runs
on green jobs as well as red ones. That step runs `summarize` from
[`scripts/ci_telemetry.py`](../scripts/ci_telemetry.py). It re-reads files the job already wrote. It
calls no device and no network, so it costs well under a second. Its output is
`runs/diagnostics/ci-telemetry/ci-telemetry.json` plus a copy of every run manifest. The action
uploads that directory as its own small artifact, `ci-telemetry-<job>-<attempt>-<suffix>`. With it,
a cross-run report never downloads videos. The record holds four groups of facts.

| Group | Fields |
|---|---|
| Keys that line jobs up | workflow, job, run id, attempt, runner name, runner image |
| Machine | OS version, hardware model, CPU count, memory, Xcode version (iOS) |
| Measurements | load from `host-telemetry.log`, render probe, stall captures per trigger |
| Each scenario | verdict, failing action, duration, first `wait`, longest `wait` |

Both load samplers also append one CPU line from a two-sample `top`, prefixed `Interval`. A
single-sample `top` reports CPU time averaged since boot. The interval line alone says how idle the
machine was at that moment.

**Candidate causes.** Each job gets the labels below when its signal is present, red or green. The
labels come from fixed thresholds over measured numbers. They hint at where to look and never touch
a verdict.

| Candidate cause | Signal and threshold |
|---|---|
| `host-resources` | load above 1.5 per CPU, measured idle below 5 percent, free memory below 10 percent, I/O wait above 20 percent, or hypervisor steal above 10 percent |
| `device-slow-or-wedged` | a device boot or wait step above 240 seconds, a pre-run screenshot that hung or took over 10 seconds, or a `runner-crash` stall capture |
| `channel-timeout` | a runner-channel retry or mid-run channel crash in the job log |
| `video-start` | an unconfirmed recording start in the log, a zero-byte render-probe recording, or a `video-no-bytes` or `screenrecord-no-growth` stall capture |
| `network-or-deps` | a network error in the log, or a failing step that merely sets up, installs, or downloads |
| `runner-contention` | a job that queued longer than 300 seconds |
| `app-wait-timeout` | a failing `wait` step with none of the signals above |
| `test-assertion` | any other failing step with none of the signals above |

The last two labels are residual. A red job with no infrastructure signal is where a real defect or a
too-tight wait shows up. A fetched red job whose log could not be read gets
`log-signals-unknown` instead, because the log signals above were never checked. Time of day and runner image are not labels. The report compares them as
dimensions instead.

**Running the analysis.** These steps run on a workstation. The GitHub command-line tool `gh` there
needs access to the repository.

1. Fetch the recent runs of one workflow. The command reads the last 30 completed runs and downloads
   their telemetry artifacts. It joins each record with facts from the API. Those facts are the job's
   conclusion, its queue time, and each step's duration. The command also counts four warnings in
   each job log:

   - channel retries
   - channel crashes
   - unconfirmed recording starts
   - network errors

   ```bash
   python3 scripts/ci_telemetry.py fetch --workflow ios-e2e.yml --limit 30 --out tmp/ci-telemetry
   ```

   The command prints `N job record(s) → tmp/ci-telemetry/records.jsonl`. A job without a telemetry
   artifact still gets a record from the API alone. A job that stopped before its `collect` step
   thus stays in the count. The command skips a cancelled job. The E2E workflows cancel a
   superseded run when a newer push arrives. Such a run says nothing about the code or the machine.
   A job that its `timeout-minutes` killed is also reported as cancelled. The command tells the two
   apart by the timeout annotation on the job, and counts a killed job as red.

2. Print the report.

   ```bash
   python3 scripts/ci_telemetry.py report tmp/ci-telemetry
   ```

   The report holds four tables:

   - each candidate cause with its count on red jobs and on green jobs
   - each metric's median on red jobs and on green jobs
   - the red rate along each dimension the record carries, such as runner image or hour of day
   - the red jobs that carry no label

   Pass `--json` to get the same data for a script.

3. Rank the scenarios themselves. The fetch output holds every manifest, and `bajutsu flakiness`
   reads that directory directly:

   ```bash
   uv run bajutsu flakiness --history tmp/ci-telemetry/manifests
   ```

**Reading the report.** A cause is worth fixing when its red count is high and its green count is
low. A cause that shows up as often on green jobs explains nothing about the red ones. A red rate
that jumps on one runner image or hardware model points at the machine, not the code. A red job left
with no label goes to [`investigate-ci-failure`](../.apm/skills/investigate-ci-failure/SKILL.md).
That skill reads the job's full `runs/` artifact.

## Running bajutsu in your app's CI

> bajutsu is pre-release (unpublished). Until it is on PyPI, vendor it (a submodule or a
> checkout) and run the action from that checkout — the action runs `uv sync` against
> bajutsu's `pyproject.toml`.

bajutsu produces CI-ready output: a `junit.xml`, a self-contained `report.html`,
a `0` / `1` exit code, and — inside Actions — failure **annotations** + a job **summary**
(see below). On a macOS runner, follow these two steps:

1. **Build and install your app** (and the XCUITest runner) onto a booted Simulator (this varies
   per app, so it stays yours — `xcodebuild` + `xcrun simctl install`).
2. **Run bajutsu** with the [`bajutsu-e2e`](../.github/actions/bajutsu-e2e/action.yml)
   composite action — it syncs deps, runs your scenarios (with `run --score`, so the log carries
   the entry-screen convention grade), and uploads the run (report + screenshots + video +
   `network.json`) as an artifact. The XCUITest backend needs no pip extra — its runner is driven
   over HTTP and `xcodebuild` ships with Xcode on the runner.

```yaml
jobs:
  e2e:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: maxim-lobanov/setup-xcode@v1
        with: { xcode-version: latest-stable }
      - uses: astral-sh/setup-uv@v6
        with: { enable-cache: true }

      # --- build + install your app (your build, on a booted Simulator) ---
      - run: xcodebuild -scheme MyApp -destination 'generic/platform=iOS Simulator' -derivedDataPath dd build
      - id: sim
        run: |
          udid=$(xcrun simctl create ci "iPhone 16")
          xcrun simctl boot "$udid"; xcrun simctl bootstatus "$udid" -b
          echo "udid=$udid" >> "$GITHUB_OUTPUT"
      - run: xcrun simctl install "${{ steps.sim.outputs.udid }}" dd/Build/Products/Debug-iphonesimulator/MyApp.app

      # --- run bajutsu ---
      - uses: your-org/bajutsu/.github/actions/bajutsu-e2e@main
        with:
          scenarios: e2e/*.yaml
          app: myapp
          udid: ${{ steps.sim.outputs.udid }}
```

### Failure annotations + job summary

When `GITHUB_ACTIONS` is set, `bajutsu run` emits a `::error::` annotation per failed
scenario (shown inline on the PR) and appends a PASS/FAIL table to `$GITHUB_STEP_SUMMARY`.
No flag needed — it auto-detects the Actions environment.

### Notes

- **JUnit**: `junit.xml` is written next to the report; feed it to a test-reporter action
  (e.g. `dorny/test-reporter`) for an inline test view.
- **Determinism**: use scenario [`mocks`](network.md#deterministic-mocks) to stub the
  network so runs do not depend on a live server.
- **Convention score**: the composite action passes `run --score`, so the run prints the app's
  entry-screen convention grade (`Ready` / `Partial` / `Blocked`, the same score `doctor` reports)
  to stderr, computed from the run's own first launch. It is diagnostic only — never on the pass/fail
  path — and folding it into the run avoids a separate `doctor` step that would cold-spawn a second
  XCUITest runner. Run `bajutsu doctor` locally for the fuller preflight (runnability + capability
  checks); a hard env/permission runnability gate in CI (`xcodebuild` / Xcode presence) is future work.
- **Affected-step selection**: [`bajutsu impact`](cli.md#impact) reports which scenario steps a `git`
  diff is likely to affect, so a pipeline can order those steps first for fast feedback. The safe
  default is additive — run the whole suite, just affected-first. Narrowing a pre-merge run down to the
  affected set is opt-in and sound only with both safeguards `impact` documents: fall back to the full
  suite whenever the report is **incomplete** (`--json`'s `complete` is `false`, i.e. the diff carried
  a change that maps to no referenced id), and still run the full deterministic suite at a coarser
  cadence (on merge, nightly, or at release). The pass/fail verdict always stays with `run`.
