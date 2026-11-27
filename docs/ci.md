# Continuous integration

This document covers two separate topics:

1. **CI for this repo**: guarding the tool itself (`.github/workflows/`).
2. **Running bajutsu in *your* app's CI**: a composite action and a recipe you reuse.

## This repo's CI

The table lists each workflow with its runner and its triggers. The sections after it describe what each workflow does, in the same order.

| Workflow | Runner | When |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) (`check` job) | Linux | push to `main`, every pull request (PR) |
| [`mcp-wire.yml`](../.github/workflows/mcp-wire.yml) | Linux | manual + PRs touching `bajutsu/mcp/**`, the `bajutsu mcp` command, or the wire test |
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

This workflow is the **Model Context Protocol (MCP) wire round-trip** lane (BE-0301). It has one job, **wire (stdio)**.

The job starts `bajutsu mcp --transport stdio` as a real subprocess. The `fastmcp.Client` of the `mcp` Software Development Kit (SDK) drives the subprocess over stdio. The command is `pytest tests/test_mcp_wire.py -m mcp_wire -n0`.

The job exercises three behaviors that the in-process `tests/test_mcp.py` never touches:

- the `JSON-RPC` framing
- the tool-schema advertisement
- the encoding of each resource's Uniform Resource Identifier (URI)

Concretely, it lists tools and round-trips a `bajutsu_doctor` / `bajutsu_run` call. It also reads a run-evidence resource. Every check runs against the `fake` backend.

- `bajutsu_doctor` runs real device-free logic in the server.
- `bajutsu_run` spawns a real `bajutsu run` whose verdict depends on the environment. The round-trip asserts that the verdict line survives the transport, and nothing more.

The `mcp_wire` marker keeps the suite out of the fast gate (pyproject `addopts` `not mcp_wire`). A real subprocess that talks over stdio is more timing-sensitive than an in-process call. The lane thus lands as a **non-gating** per-PR signal. It becomes required once stable, the precedent BE-0282's `network (playwright)` set.

### `web-e2e.yml`: the web (Playwright) backend lane

This workflow is the **web (Playwright) backend** lane (BE-0279). It runs six jobs against a headless Chromium:

- **smoke (playwright)** runs the `demos/web` scenarios (`make -C demos/web e2e`).
- **dogfood (serve UI)** drives Bajutsu's own serve web app (BE-0058).
- **conformance (playwright)** runs the BE-0114 driver contract against the real browser.
- **network (playwright)** drives the real network path (`make -C demos/web e2e-network`, BE-0282). It covers `page.route` interception, `requestfinished` capture, and the `mocked` flag. It also covers masking of evidence captured for real.
- **codegen (playwright)** generates a native Playwright test from a scenario (BE-0293). The command is `make -C demos/web codegen-e2e`. The real `@playwright/test` runner runs the test. The job is the web twin of `ios-e2e.yml`'s `codegen (xcuitest)`.
- **onboarding (doctor / provision)** exercises `bajutsu.common.provisioning.provision` end to end. It runs on a machine that genuinely has no browser. It then re-checks `doctor`'s environment gate after provisioning (BE-0304).

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

The lane caps reuse at one per runner (`BAJUTSU_XCUITEST_MAX_WARM_REUSES: 1`). The cap curbs a runner that exits mid-run on contended CI machines.

The twelve scenario documents that run are every scenario the seven files hold. BE-0315 made the notification grant native, so nothing is `ai`-tagged to exclude. The documents pay six cold `xcodebuild test-without-building` startups rather than one. That is two more than the four separate jobs they replaced summed to. Each of those jobs also paid its own Swift build and boot. The shared `build` job now pays that cost once for the whole lane.

The scenarios cover:

- the Stable catalog (`smoke.yaml`)
- the pinch/rotate two-finger gesture (`gestures_multitouch.yaml`, BE-0019)
- the runner channel's `/type` (`search.yaml`) and `/swipe` + `/back` (`notices.yaml`, BE-0281)
- each button of a presented `UIAlertController` (`alert.yaml`). The scenario alone reaches `uniquelyIdentifiedElement`'s duplicate-group branch. `swift test` cannot compile that branch.
- `permission.yaml`: the deterministic `permissions` field. Since BE-0315 it also covers the native reactive `systemAlertHandling` notification grant. It covers BE-0276 location and BE-0315 notification, both now on the gate.
- the explicit `handleSystemAlert` step (`permission_system_alert.yaml`, BE-0316). It taps the "Allow" button of the SpringBoard notification prompt. The tap uses an accessibility query and no vision model.

`run` runs each scenario to its own verdict. It reddens the gate if any scenario fails. The per-scenario pass/fail thus moves from job names into the run's report.

**actuation (xcuitest)** is the lane's non-gating landing pad. It drives on-device XCUITest actuation that `run` has not absorbed yet (BE-0281):

- `back` (`navigation.yaml`)
- device control (`setLocation` / clipboard / `push`, `device.yaml` + `push.yaml`) on the Stable launch tab
- the foreground notification banner that the interruption handler clears (`push_banner.yaml`, BE-0416)
- the four text-editing actuators (`text_editing.yaml`)

It also drives the scenario-authoring features on a third actuating backend (BE-0285):

- `extract` capturing the Log tab's live counter (`extract.yaml`)
- `forEach` iterating the five Stable rows (`foreach.yaml`). The loop body pushes and pops a detail, so the tree mutates between iterations.
- data-driven rows (`data_driven.yaml`)
- `relaunch` (`relaunch.yaml`)

Those four are the shared showcase files, unchanged (BE-0221). The list puts them ahead of the device-control scenarios. Neither a delivered notification nor an OS-level grant then precedes them.

Two scenarios come last in the list.

`system_alert_handling.yaml` checks one claim. The reactive `systemAlertHandling.rules` guard alone answers three prompts. They are notification, tracking, and paste. The scenario uses no `handleSystemAlert` step. The file covers notifications denied. It also covers tracking and paste, each granted and denied. The deny scenarios show that the rule answered. XCUITest's own default handler taps the default button. That handler cannot produce a denied state. As new on-device coverage, the file starts here for the same reason as the next one.

`paste_system_alert.yaml` (BE-0369) covers the lane's cross-process paste. The explicit step answers it. `setClipboard` seeds the pasteboard from outside the app. The app's own read then raises iOS's paste-consent prompt. `handleSystemAlert: { prompt: paste }` answers that prompt. It uses a native accessibility query. The scenario covers granted and denied. Each runs under the target's locale and again under `locale: ja_JP`. The pair checks the label lookup against a SpringBoard in another language than the run's own. The scenario is new on-device coverage. It earns its stability here before any promotion into `run`.

**golden (xcuitest)** runs the BE-0006 element-tree golden over XCUITest (`golden.yaml`). The job is the iOS twin of `android-e2e.yml`'s `golden (adb)`.

**codegen (xcuitest)** generates a native XCUITest from a scenario (`make -C demos/showcase ui-test`). It runs the test with `xcodebuild`, with no bajutsu or AI at test time. It compiles the UITests Scheme, a different product than the resident runner. The job builds its own product and is not a `build`-artifact consumer.

**bundled-runner (xcuitest)** runs a smoke scenario against both the SwiftUI and UIKit a11y apps. The runner resolves from the wheel bundle rather than a config `testRunner` (BE-0292). That proves the runner is app-agnostic.

**conformance (xcuitest)** runs the driver conformance suite (BE-0114). It runs on-device against the XCUITest backend.

- The suite recovers from a Simulator infrastructure fault the way `bajutsu run` does. Such a fault is a resident runner that exits abnormally, `base.BackendCrashError`. The suite re-leases a fresh device and re-runs the affected test. Two limits bound each test. One is the retry count (`BAJUTSU_CRASH_RETRIES`). The other is the wall-clock respawn budget (`BAJUTSU_CRASH_RECOVERY_BUDGET`).
- This required check thus reddens on a real contract violation, not a flake of the machine. An abnormal exit on every attempt still ends the test with an explicit error.
- The suite counts every respawn into the uploaded `conformance-recovery-report` artifact (BE-0334).
- A wedged CoreSimulator is the machine's fault, but not a respawn's to repair. Rebuilding the device consists of the same `simctl` calls that stalled a moment ago. The suite names the wedge a machine fault in the job log. It counts the wedge into the same artifact instead of retrying.
- The suite resolves the app's data container once per lease rather than once per test. That one read gets a second attempt when the first exceeds its deadline.
- A stall that the second attempt absorbs reddens nothing. The suite counts it in the artifact as its own leading indicator. A machine that wedges the check later shows this stall getting worse first (BE-0378).

**fault-injection (xcuitest)** signals the process that runs the runner (BE-0305). It does not drive a healthy runner:

- A brief `SIGSTOP` stalls the channel the way a wedged runner does. The transient retry (BE-0207) must absorb the stall.
- Recovery from an abnormal runner exit (BE-0287) must ride out a freeze past the retry budget.
- A `SIGKILL` must end the read on a diagnosis of an abnormal exit. The diagnosis must name a mid-run runner fault, not an unrelated timeout.

Each case lifts its fault on the channel's own log record that it reached the layer under test. No case relies on a guessed delay.

The job landed as a per-PR signal. A defect in the runner's Hypertext Transfer Protocol server had been reddening it. Once the fix for that defect landed, the job joined `E2E (iOS)`'s `needs:`. It has passed 64 runs in a row since.

**visual (xcuitest)** pixel-compares the Stable catalog against the committed `baselines_ios/` baseline. The command is `make -C demos/showcase e2e-visual`. It masks the status bar and the "Liquid Glass" tab bar. Its `e2e-visual` rebuilds the app itself. Like `codegen`, the job is not a `build`-artifact consumer.

**network (xcuitest)** drives the real network path over the iOS transport (BE-0282). The command is `make -C demos/showcase e2e-network`. The job is the iOS twin of `network (playwright)` in `web-e2e.yml`. The same holds for `network (adb)` in `android-e2e.yml`.

- BajutsuKit's in-app `URLProtocol` answers a mocked `POST /post` with a 201 status. A live server would answer with a 200 status. The protocol reports each exchange to the collector on loopback.
- A second scenario issues an unstubbed catalog `GET`. It asserts that the job observed the exchange, and nothing more.
- `demos/showcase/network/assert_network_evidence.py` asserts the persisted `network.json`. The mocked exchange's `Authorization` header and `password` body field must show masked values. No raw secret may appear anywhere in the file. The unstubbed exchange must carry `mocked` false. An over-broad mock matcher thus cannot claim traffic that nothing stubbed.

The masking check is the gap BE-0282 exists for. Every pure masking test feeds the algorithm a hand-built exchange. Nothing else on iOS observes a credential captured for real. This check alone sees whether shipped evidence masks it. The job drives a `make` target that builds what it needs. Like `codegen` and `visual`, the job is not a `build`-artifact consumer.

The lane carried a two-device **pool (xcuitest)** job (BE-0298) until the job was withdrawn. The job booted a second Simulator. It asserted the device pool's isolation claim across one `bajutsu run --workers 2`. It returned that verdict once in five runs.

The other four runs collapsed on the machine rather than on any pool check:

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

This workflow is the **Android (adb) backend** lane (BE-0208). It runs twelve per-concern jobs that mirror the iOS and web lanes' job splits. Each job boots its own Android Virtual Device (AVD) with `reactivecircus/android-emulator-runner`. The AVD runs Application Programming Interface (API) level 34 on an x86_64 image. The Linux Kernel Virtual Machine (KVM) accelerates it. A thirteenth job, **warm gradle cache (adb)**, has no AVD.

The lane needs **no Mac / Simulator**. The lane is the third backend's Linux twin of the iOS and web e2e lanes. A `changes` detector path-gates it (`scripts/e2e_changes.py`, `E2E_LANE=android`). The detector feeds the required `E2E (android)` aggregator (BE-0279).

#### Android emulator packages

Every job that boots an AVD installs two packages before its first emulator step. They are the Android Emulator and the system image. It uses the [`install-android-emulator-sdk`](../.github/actions/install-android-emulator-sdk/action.yml) composite action. The `sdkmanager` command exits 0 with a warning when it cannot unpack a downloaded archive. A damaged download thus used to surface as a later `adb` `Connection refused` error. The `scripts/install_android_sdk_packages.sh` script retries the install once. A second unsuccessful attempt stops the step and names the package.

#### Android Gradle warm-up

**warm gradle cache (adb)** builds one set of APKs up front. The set is the Compose + resident-server APKs. `smoke`/`golden`/`conformance`/`fault-injection`/`pool` all build them. Those five jobs' own unchanged builds then restore a warm Gradle dependency and build cache. They no longer resolve and compile cold five times over.

- The job skips Views. `smoke` alone needs Views. Warming Views here would make the other four jobs wait on a compile none of them uses. The wait comes from a job-level `needs:`. `smoke`'s own Views build stays cold, the same as before this job existed.
- `actions/cache` cannot share a still-unsaved cache between concurrent jobs of one run. The warm-up runs first, and those five jobs declare `needs: warm-gradle-cache`. None of them passes `cache-read-only` explicitly. They rely on the composite action's own default. That default is safe: `true`, the mode that reads the cache without writing it. `gradle/actions` documents this multi-job cache-sharing pattern.
- `network`, `visual`, and `codegen` stay out of that `needs:` on purpose. `codegen` also needs the Compose AndroidTest/instrumentation variant. warm-gradle-cache never builds that variant. A job-level `needs:` would also block their own unrelated AVD-cache step until the build finished. That costs those lighter jobs more than a cache hit could save.
- A cache miss or an outright red warm-up costs time and never correctness. Every consumer job's `if:` thus uses `!cancelled()`. It does not inherit a hard `needs` dependency on the warm-up's success. It does not use `always()`. That function would survive this file's own `concurrency: cancel-in-progress`. A superseded run's jobs would keep dispatching. The warm-up never joins `E2E (android)`'s `needs:`.

#### Android jobs

- **smoke (adb)** builds the Compose and Views showcase APKs. It runs the Stable-tab scenarios through `--backend android` (`make -C demos/showcase/android e2e`). The scenarios are the core id/tap/type/value flows. A flow that pushes a screen and navigates back joins them.
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
- The single-touch gesture flow (`gestures`) joins too. On a rooted emulator, the adb driver drives a double-tap with a raw `sendevent` touch sequence. The `e2e` target runs `adb root` first. The sequence delivers both taps inside the platform double-tap window. A per-tap `input` run starts a fresh Java Virtual Machine (JVM), which overran that window. On a non-rooted device the driver falls back to `input tap` unchanged. The flow's reveal swipes rest on unit 5's other half. The default directional swipe travels a screen *fraction*. The constant is `_SWIPE_FRACTION` in `bajutsu/common/orchestrator/actions/handlers/gestures.py`. It covers the same proportion of Android's dense screen (2400px) as of iOS's (~900pt). A fixed coordinate count did not (BE-0208 unit 5).
- The multi-touch gesture flow (`gestures_multitouch`) joins too (BE-0232). On the rooted emulator, the adb driver drives a pinch / rotate as a raw two-slot `sendevent` sweep. Two contacts move together across interleaved frames. The shared scenario that iOS runs on XCUITest thus runs unchanged on Android. Nothing can approximate a two-finger gesture. Unlike the single-touch double-tap, there is no `input` fallback. The flow requires root and fails with an explicit error otherwise.
- The runtime-permission flow (`permission`) joins the lane too (BE-0208 unit 6). It exercises BE-0210's up-front grant. The flow uses the **same** `permission.yaml` the iOS lane runs. The grant mechanism lives in config, not the scenario, so one file serves both. `showcase-compose` grants `POST_NOTIFICATIONS` up front (`grantPermissions` → `pm grant` at lease time). Android's `RequestPermission` contract then short-circuits to granted with no dialog. The scenario's `systemAlertHandling` guard never triggers here. The flow thus stays deterministic on the lane. It uses no Large Language Model (LLM) and no fixed sleep. On iOS, notifications cannot be pre-granted, so that guard taps "Allow" instead.
- The device-control flow (`device`) joins the lane too (BE-0208 unit 5). It overrides the Global Positioning System (GPS) location with `emu geo fix`. It also round-trips the clipboard. It then re-asserts the settled screen. The flow uses the **same** `device.yaml` the iOS lane runs. Both platforms advertise `setLocation` and the clipboard. That unifies the file across iOS and Android. The `push` half lives in `push.yaml`. The flow runs on the Stable launch tab. The smoke job also runs `push.yaml` and `push_android.yaml`. They deliver a push through BajutsuAndroid's push receiver. They assert the title that the app's handler shows (BE-0498). It exercises both `setLocation` and `clipboard` of the device-control family. `cmd clipboard` is a silent no-op on-device. Since Android 10, the foreground app alone may touch the clipboard. The clipboard thus runs through an in-app receiver (BE-0233). The showcase embeds the receiver from `BajutsuAndroid`. The seed/read-back is the strong assertion PR #934 wanted.
- The location flow (`location_android`) joins the lane as well (BE-0481). `e2e` runs it over the emulator's own serial, which takes `emu geo fix`. A separate **location over adb connect (adb)** job reruns it through `e2e-location-connect`. The rerun uses the same kind of emulator, reached with `adb connect`. That job is a per-PR signal until it has a run history. Until then it stays outside `E2E (android)`'s `needs:`, as `golden` and `app-crash` did. A `<host>:<port>` serial has no console that adb can reach. Thus `setLocation` goes through BajutsuAndroid's in-app location receiver. A real device takes the same route.
- The interrupt-handler flow (`interrupts`) joins the lane too (BE-0314). The flow is a syntax demo. Its `interrupts` entry never triggers against the real app. The flow underneath is the same stable→horse→favorite one `firstlook` already runs. What it adds over `firstlook` is BE-0314's *check* path against a live tree. A `wait` rides its own poll tree at no extra cost. A bare act step pays one extra query for the guard. This scenario declares no `screenChanged` policy. A never-matching handler is thus proven on a real device to disturb nothing. It costs one bounded read per act. No fast-suite fake can show that. The scenario's ids carry two forms (BE-0221). One is the dotted form that the showcase `SPEC.md` defines. The other is the underscore form of Android Views. It runs unmodified.

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

`fault-injection (adb)` walked that same path to its end. It went red once in 73 runs. The emulator never came up, which is not anything the lane asserts. `conformance (adb)` went red four times in 67 runs over the same span. That record moved `fault-injection (adb)` out of signal status and into the aggregator's `needs:`. A regression in the retry's real detection now blocks a merge. Before, it surfaced as a red that a merge could ignore (BE-0305).

`tests/test_e2e_gate_needs.py` pins three properties on the fast suite:

- each aggregator's composition
- that the aggregator reads the result of every job a gate `needs:`. A red dependency must not pass as the path-skip, which the aggregators count as green
- that each of those results actually reddens the check, by running the verdict script itself

### `devicefarm.yml`: the AWS Device Farm batch submit

This workflow is the **Amazon Web Services (AWS) Device Farm batch submit** (BE-0235). It builds the showcase Compose Android Package Kit (APK). It packages Bajutsu, the config, and the scenarios. It then hands the package to [`scripts/devicefarm_submit.py`](../scripts/devicefarm_submit.py). The script does the following:

- It uploads a custom-environment test spec. The spec runs `bajutsu run --backend adb` on Device Farm's machine.
- It polls the run.
- It downloads the artifacts.
- It surfaces **Bajutsu's own manifest verdict**, never Device Farm's classification.

The workflow is CI-side glue outside the deterministic core, so no LLM touches the verdict. It runs on `workflow_dispatch` alone, never on push/PR, and is not a required check.

Authentication is a short-lived AWS credential, `AWS_DEVICEFARM_ROLE_ARN`. It comes from GitHub's OpenID Connect. A `devicefarm` Environment scopes the credential. The project and device-pool ARNs live in repository variables. With any of them unset, the job is a green no-op. It stays dormant until an operator wires up an account.

The real-account serial-resolution proof of concept (PoC) is a documented human procedure. See [AWS Device Farm](devicefarm.md). The procedure stays off the deterministic gate on purpose.

### `ai-smoke.yml`: the live-model lane

This workflow is the **live-model** lane. It has two jobs. Both are **non-gating** signals (the BE-0282 precedent).

**smoke (direct Anthropic API)** (BE-0300) calls a real provider. The call goes through Bajutsu's own adapter code, `bajutsu.ai.anthropic.AnthropicBackend`. It sends a minimal forced-tool prompt. It asserts that the reply lands as a populated, parseable neutral `MessageResponse`. The command is `pytest tests/test_ai_backend_live_smoke.py -m live -n0`. The assertion covers nothing beyond that. Every other adapter test drives a hand-written `FakeAnthropic`. Nothing else re-observes the live API's actual shape. The job is a transport-and-schema check, never a model-quality one.

**accuracy (system-alert guard)** (BE-0308) asks the other live question. It asks whether the answer is right, not whether the transport round-trips. The job shows real iOS system dialogs to the vision alert locator. The locator is `bajutsu.agents.alerts.ClaudeAlertLocator`. The showcase app supplied the dialogs. The assertion is that the locator's coordinates land inside the right dismiss control's frame. The command is `pytest tests/test_real_model_alerts.py -m live -n0`. That claim is the guard's own safety claim. Every other test of the guard assumes the claim without checking it. Those tests hand the guard an `AlertDecision` that its author typed.

The dialogs are captures stored under `tests/fixtures/be0308/`:

- a notification prompt
- a location prompt
- iOS's cross-process paste consent
- the showcase app's own delete confirmation. The guard must not reach for its destructive button at all

The captures need no Simulator, so the model call is the one live part. A deterministic check that each capture is self-consistent runs on every gate without a credential.

Neither job puts an LLM on the `run` / CI verdict (prime directive 1). Both exercise the AI authoring periphery alone. The `live` marker keeps both suites out of the fast gate (pyproject `addopts` `not live`).

Both jobs run on `workflow_dispatch` alone, never on push/PR. A fork-triggered run thus can never see the credential (the boundary `devicefarm.yml` draws). The credential is the `ANTHROPIC_API_KEY` repository secret, scoped to the `ai-smoke` Environment. With the secret unset, every test skips itself and both jobs are green no-ops. The lane stays dormant until an operator wires the secret up.

The lane wires the direct Anthropic API adapter alone. Bedrock needs a live AWS role. `ant` needs a signed-in OAuth Command Line Interface (CLI) seat. Neither is realistically a CI secret. Their `-m live` tests still run locally or manually.

### Which E2E checks gate a merge (BE-0279)

Each backend lane carries one always-reporting aggregator. That aggregator is the lane's required
check:

- iOS: `E2E (iOS)`
- Android: `E2E (android)`
- web: `E2E (web)`

Per-backend aggregators keep attribution, so a red check names the backend that broke. A check
**gates a merge if, and only if, its result is deterministic and independent of the machine**. Some
checks depend on the machine or on an upstream dependency. Such a check stays a non-required signal.
It still runs and surfaces a drift on its own job, but it never blocks a merge.

| Check | Jobs | Why it stays out of the aggregators' `needs:` |
|---|---|---|
| Pixel Visual Regression Testing (VRT) | the `visual` jobs | A pixel baseline is host-specific. A Simulator or emulator renderer varies by OS, device, and toolchain. A drift then has no tie to any Bajutsu change. |
| Element-tree golden | the iOS and Android `golden` jobs | The golden is deterministic. On Android, though, an upstream on-device server reads the tree, and its drift could redden the golden apart from any Bajutsu change. A per-PR signal suits such a drift better than a merge blocker. |

None of the lanes path-gate at the trigger. A `paths:` filter can skip a required check.
A skipped required check stays pending forever and blocks the merge. Each lane instead triggers on
every PR and on the merge queue. A `changes` job then path-gates the heavy jobs. That job runs
[`scripts/e2e_changes.py`](../scripts/e2e_changes.py) with `E2E_LANE=ios|android|web`. The script is
the per-lane relevance filter, and `tests/test_e2e_changes.py` unit-tests it. The aggregator runs
`if: always()`. A path-skip thus reports as a pass, and the lane neither runs nor blocks an unrelated
PR. A new required aggregator also needs an entry in the branch-protection ruleset of `main`. A
maintainer makes that entry outside the repository, with the exact check name.

The filter inverts its default for `bajutsu/` (BE-0333). The old filter kept a positive list by hand.
A file had to join that list before any lane saw it, and an unlisted file triggered nothing. The
shared core now sweeps the *whole* package. It carves out nothing but what a list classifies
explicitly. Two lists do that classifying:

- `_PERIPHERY_EXCLUSIONS` names the periphery that no lane exercises. Each entry carries the reason it
  stays out. The entries cover the analytics and analysis stacks, the MCP server, and the AI adapters.
  They also cover the GitHub and cloud integrations. The rest are periphery modules of the mixed
  `agents`, `crawl`, and `cli.commands` packages.
- `_LANE_CLAIMED` names the per-backend leaves that each lane reclaims.

The sweep takes in a file that neither list names. That file triggers all three lanes until somebody
classifies it. The cost is a wasted job, which is the safe direction. The positive list produced the
opposite, a silent under-trigger. The new default retires a whole class of miss.

The misses it retires:

- a top-level module split into a package. `bajutsu/config` and `bajutsu/platform_lifecycle` both
  drifted that way. The lane then skipped the fleet it exists to exercise.
- a new top-level module or command
- files that the run path imports but the list never named. `bajutsu/common/report/` leads that
  group, since every run calls its manifest writer.

Each of these now triggers the lanes, because a package and a module match the sweep alike.

Five tests keep the classification honest:

- `test_run_path_closure_is_gated_or_excluded` walks the static `ast` import closure of the run path.
  The test fails when a file it reaches belongs to no lane and to no periphery entry. Suppose the run
  path *starts* importing a module that nobody classified. `make check` then turns red. Without the
  test, the module would surface months later. A required check would stay green for no visible
  reason.
- Two more tests resolve every exclusion entry and every plain path against the tree. One is
  `test_periphery_exclusion_paths_exist`. The other is
  `test_every_plain_literal_path_in_the_filter_exists`. A rename or a deletion then
  turns the gate red rather than leaving a pattern that matches nothing.
- The shared core sweeps the two per-backend directories, minus the leaves each lane names. Those
  directories are `bajutsu/common/drivers/` and `bajutsu/platform_lifecycle/environments/`. One test
  asserts that no file under either directory triggers zero lanes. A newly added backend module then
  over-triggers rather than going unseen.
- Another test asserts that at least one lane reclaims each `_LANE_CLAIMED` leaf. A leaf carved out
  but claimed by no lane turns the gate red.

We measured the over-trigger cost of the inverted default before it shipped. We replayed the last 80
merged pull requests. On every one of them, the sweep decided each lane as the old list did.
`scripts/e2e_overfire_report.py` produces that measurement.

The `changes` job narrows one step further in the one case it can prove safe (BE-0322). Take a change
confined to a lane's scenario files. The change triggers the jobs that declare a changed scenario.
The rest of the lane stays idle. The filter builds a map from each changed scenario to the jobs that load it:

- For most iOS jobs, the filter reads the `scenarios:` that each job declares in the workflow.
- The `codegen` and `visual` jobs name their scenarios in a Makefile target, not in a workflow input.
  For those two, the filter reads the scenarios that their targets in `demos/showcase/Makefile`
  run. BE-0338 added that read.

The map thus never drifts from what those jobs run. The filter emits `relevant`, a `shared`
flag, and an `affected` job array. The jobs guard on the last two. A test pins the attribution it reads
from the Makefile against those targets. A target that gains or loses a scenario then turns the gate
red unless the map moves with it.

Every other case still triggers the whole lane:

- a shared-code change. Driver, runner, app, or workflow code can affect any scenario.
- the `conformance` dimension job, which declares no scenario subset. That job drives the whole
  driver-conformance harness.
- a lane whose jobs carry no scenario keys at all, such as Android and web

The decision over-selects toward the whole lane, so it never skips a job that a change could break.
It reads three inputs: the `git` diff, the workflow's own declarations, and those Makefile targets.
It puts no LLM on the path and has no bearing on any run's pass/fail verdict.

The dev tools live in the `dev` dependency group. The Linux job thus runs `uv sync --group dev`, then
`uv run --no-sync …`. A plain `uv run` would re-sync to the default set and drop the dev tools. The
gate mirrors [`make check`](../Makefile) and the [`pre-push`](../.githooks/pre-push) script step for
step. Two tools sit outside `uv`: `actionlint` and `gitleaks`. CI installs both separately, and each
step skips with a notice when its tool is absent. Every other check runs the same way on a fresh
clone through `uv` alone. That parity makes "green locally" predict "green in CI". The skill-drift
check belongs to the `uv` half, not to the exceptions. `apm-cli` is a `dev` dependency (BE-0390), so
`make lint-skills` has no skip branch.

### Every job declares a timeout

Every job under [`.github/workflows/`](../.github/workflows/) sets `timeout-minutes`. A job that
declares none gets GitHub's own default, which waits 360 minutes before cancelling it. A stalled job
then holds a runner for most of a day before a red check appears. It spends Actions minutes all
that time. The web
lane has already paid that cost. GitHub cancelled `conformance (playwright)` and `codegen
(playwright)` at almost four hours apiece. Each of those jobs takes two to seven minutes when it
passes.

Some jobs derive their bound from their own arithmetic. Each such job sets its own value and gives
the reasoning in a comment beside it.

- 180 minutes for the AWS Device Farm submission.
- 60 minutes for the iOS scenario jobs.
- 30 minutes for the Android `smoke` job.
- 25 minutes for the other Android scenario jobs.
- 20 minutes for `claude-review`.
- 15 minutes for the Android Gradle-cache warm-up.
- 15 minutes for each of the two live-model jobs in `ai-smoke`.
- 10 minutes for `mcp-wire`.

Every job without such reasoning takes the **30-minute** repository default. That default clears
the slowest passing job we have measured. That job is the Swift analysis of CodeQL, at about 20
minutes. It still
turns a stall into a red check inside the half hour.

Two jobs carry no `timeout-minutes` of their own. One lives in
[`docs-refresh.yml`](../.github/workflows/docs-refresh.yml).
The other lives in [`roadmap-refresh.yml`](../.github/workflows/roadmap-refresh.yml).
Both call the reusable [`refresh.yml`](../.github/workflows/refresh.yml) workflow. GitHub does not accept the key on such a
job at all. The 30-minute bound on the own job of `refresh.yml` covers both callers instead.

### What a failing E2E job collects (BE-0361, BE-0367)

The hardest E2E cases to diagnose are the ones where no process exits abnormally.

- On **iOS**, the resident XCUITest runner reports `Timed out while requesting screenshot`. It then
  ends its test method. The Python side then declares that the runner stopped mid-run. That runner is the
  XCTest process that `bajutsu` spawns with `xcodebuild test-without-building`. `bajutsu` drives it
  over a loopback Hypertext Transfer Protocol channel.
- On **Android**, the resident UI Automator server stops answering. That server is the on-device process
  that the adb backend reads the element tree from. The emulator's own renderer can wedge instead.
  The job can also run out its `timeout-minutes`.

No process exits abnormally in any of those cases. A sweep of the diagnostic reports thus finds
nothing. The device's
render service may have wedged, or the machine may have starved it. The state that would tell the two
apart disappears before anyone opens the job.

Both lanes answer that question the same way. They collect in three layers, split by what each layer
can see.

| Layer | What it records | Why this layer |
|---|---|---|
| Inside `bajutsu` | the moment of a stall | The running process alone knows *when* a stall happens. |
| From CI, about the device | the device's state | Only a step holding that device can read it. |
| Over time, about the machine | the machine's load | Only a sampler running alongside the job can record it. |

Every artifact lands under `runs/diagnostics/`, which the jobs already upload. The per-scenario
evidence of an abnormal runner exit, described below, is the one exception. That evidence belongs to
one scenario rather than to the job. It thus lands in the evidence directory of that scenario. None of the collection reaches a
verdict. The collection writes files and nothing else. The deterministic assertions still decide
pass/fail.

One environment variable arms the first layer on both backends. `BAJUTSU_STALL_DIAGNOSTICS` names
the directory that receives the captures. The variable carries no backend prefix on purpose. A
single operator setting then turns the capture on for whichever backend a job drives.

What a capture *reads* differs per backend, because the two backends share no command:

- A Simulator answers to `simctl`, and a macOS machine to `vm_stat`.
- An emulator answers to `adb`, and a Linux machine to `top`.

Each backend contributes its own set of probes to one shared capture. The shared capture owns
everything else:

- the opt-in gate
- a wall-clock budget per capture
- a budget of two captures per trigger
- the summary that records each probe's outcome.

The budget applies per *trigger*. One frequent trigger thus cannot use up the whole budget. The
budget stays for the abnormal exit that the capture exists to explain. The video trigger was that
frequent one. It went off on every scenario of these runners, green runs included. The confirmation it went off from
watched the wrong signal. The video trigger no longer does that. A run whose runner keeps
restarting still has two or more triggers competing. The split thus stays. The variable stays unset
everywhere but these two lanes, and unset, the capture does nothing at all.

#### The iOS lane

Every iOS job that boots a Simulator collects the three layers into `runs/`. `codegen` is the
exception. Its sole artifact is its `.xcresult`, so the collection has no `runs/` directory to
ride there.

- **Inside `bajutsu`.** `BAJUTSU_XCUITEST_RESULT_BUNDLES` gives every runner spawn a
  `-resultBundlePath`. The bundle `runs/runner-logs/result-<udid>-<port>.xcresult` then records what
  testmanagerd itself saw. That record holds the precise XCTest error and its timestamps, not the
  paraphrase in the captured stdout.
  - The same argv pins `-collect-test-diagnostics never`. Left at its `on-failure` default,
    `xcodebuild` embeds a Simulator `system.logarchive` in the bundle. We measured that archive at
    163 MB for a single spawn. The targeted extracts below exist to replace that whole-archive
    collection.
  - A host variable named `BAJUTSU_XCUITEST_RUNNER_PROCESS_ENV_<NAME>` reaches the runner process
    as `<NAME>`, since `xcodebuild` does not pass its own environment through. The variable never
    overrides a value Bajutsu sets itself. The `fault-injection` and `visual` jobs use it to set
    `PERFC_LOG_REPORTS_TO_STDERR=1`. That switch makes Thread Performance Checker print its
    priority-inversion reports into the captured runner log. A follow-up step counts any report into
    the job summary and never fails the job
    ([BE-0362](../roadmaps/BE-0362-runner-http-queue-qos/BE-0362-runner-http-queue-qos.md)).
  - The stall capture triggers the moment the channel declares that the runner stopped mid-run. It
    also triggers when a `recordVideo` produces no bytes. The capture writes three files into
    `runs/diagnostics/stalls/stall-NN-<reason>-<pid>/`: a timed `simctl` screenshot, `sample` output
    for the rendering processes, and a `ps` / `vm_stat` snapshot.
- **Inside `bajutsu`, per scenario that did not pass, with nothing to opt into** (BE-0421). Take a
  scenario whose recovery retries all ran out after abnormal runner exits. That scenario gets a
  `crash-diagnostics/` subdirectory in its *own* evidence directory, `runs/<run_id>/<sid>/`.
  - The subdirectory holds the tail of the runner's captured output. The tail runs far longer than
    the twenty lines the warning folds in, but it stays bounded.
  - macOS may write an `.ips` report for the faulted `xcodebuild` process. The subdirectory then
    holds that report too.
  - The scenario's error string names the subdirectory. Nobody has to know about it in advance.
  - This evidence is the local and per-scenario counterpart to the CI sweep below. The CI sweep tars
    up the whole job's report store. That store cannot say which runner exit belongs to which of a
    dozen scenarios. The sweep also runs in CI alone. A developer who reproduces the exit with a
    local `bajutsu run` got nothing from the sweep.
  - Both halves are best-effort. `ReportCrash` may never write a report, or the machine may lack a
    `DiagnosticReports` directory. Either case leaves that entry out and turns nothing red.
  - The runner output lands for two shapes of exit that the runner's liveness signals
    name. In one, `xcodebuild` exited. In the other, `xcodebuild` lingers after its capture
    says the test run ended. A channel can also stop answering while the process runs on and writes
    no such marker. That shape is the wedged Simulator that BE-0354 escalates instead. The
    subdirectory stays empty for it.
- **When the app under test goes down**, a separate directory with a separate cause applies:
  `app-crash/`. `bajutsu` writes `app-crash/` for any scenario whose app went down mid-run. The
  directory holds the platform's own report for the app
  ([`docs/evidence.md`](evidence.md#app-crash-evidence-be-0424), BE-0424).
  - The two directories never overlap. `crash-diagnostics/` names a fault in the test
    infrastructure. `app-crash/` names a probable defect in the app under test. The app-crash path
    triggers no retry, by design.
  - Both the iOS lane and the Android lane carry a non-gating `app-crash` job. The job drives a
    showcase control that takes the app down on demand. The job sits next to `actuation
    (xcuitest)` and `golden (adb)`.
  - Newly wired on-device coverage earns its stability as a per-PR signal first. It joins the
    required aggregators after that. Every other new signal in those lanes has taken the same path.
- **From CI, about the Simulator and CoreSimulator.** Every job that drives a Simulator calls the
  [`collect-ios-diagnostics`](../.github/actions/collect-ios-diagnostics/action.yml) composite
  action.
  - Its cheap tier runs on every job and collects four items. They are the tail of
    `CoreSimulator.log` and the booted device's own CoreSimulator log directory. The tier also sweeps
    diagnostic reports beyond `.ips` and `.crash`, to the `.hang`, `.spin`, and jetsam reports that this
    class of red run leaves. Last comes a snapshot of the machine.
  - That snapshot holds `sw_vers`, `sysctl`, `xcodebuild -version`, and `simctl list`. It keys each
    red run against a runner image and a hardware generation.
  - Its heavy tier runs when the job's own run step did not pass, and not otherwise. That tier runs
    `xcrun simctl diagnose` and two targeted unified-log extracts.
- **Over time, about the machine**, from the same action's `start` phase. Every 20 seconds, a
  background sampler appends `top`, `vm_stat`, and `memory_pressure` to
  `runs/diagnostics/host-telemetry.log`. A one-shot render probe records how long a screenshot takes. The probe also records
  whether a five-second `recordVideo` produces any bytes at all. The sampler observes from outside
  every run loop. Its interval is thus a sampling cadence, not a wait that a verdict depends on. Each
  sample holds two process tables:
  - **By resident size**, from a single-sample `top`.
    A single sample has no interval to difference, so it reports every process at 0.0 percent CPU.
    Sorting it by CPU would sort on a constant.
    The rows of the first collection, sorted by CPU, never once included the app under test.
  - **By CPU over the last second**, from a two-sample `top`.
    A busy process with a small footprint never reaches the first table.
    This table names what is loading the machine.
- **Less load on the runner**: the macOS runner has three cores.
  Its Central Processing Unit (CPU) sat at 0 percent idle through whole `actuation` jobs. Green
  jobs showed the same. Four changes cut sources of that load:
  - `setup-ios-toolchain` turns off Spotlight indexing before the boot. Its `mds_stores` competed
    with the Simulator, and nothing a job runs searches the index.
  - Every `deviceLog` capture streams at the info level rather than debug
    ([evidence](evidence.md#interval-evidence-video--devicelog--apptrace)).
  - The `actuation` job narrows its scenarios' `deviceLog` captures to the app, SpringBoard, and
    `runningboardd`, through the `bajutsu-e2e` action's `log-predicate` input. Unfiltered,
    `navigation.yaml` alone streamed 455,715 lines in 80 seconds, two thirds of them from one
    system daemon. The other iOS jobs still capture every process.
  - The `run` and `actuation` jobs turn off Simulator services that no scenario needs, through the
    [`reduce-simulator-services`](../.github/actions/reduce-simulator-services/action.yml) action
    ([BE-0517](../roadmaps/BE-0517-ios-ci-simulator-service-reduction/BE-0517-ios-ci-simulator-service-reduction.md)).
    After the boot, the action runs `launchctl disable` for each label in its list and reboots the
    Simulator. The list groups the labels by category, with the reason each category can stay off.
    Each job logs the count of services turned off and of Simulator processes at its start and end.
    A manual run can pick `simulator-services: stock`, which reboots without turning anything off.
    A manual run can also pick `lanes: measure`, which runs the build, `run`, and `actuation` jobs
    alone. Each such run gets its own concurrency group, so two runs in flight do not cancel each
    other.
- **Before anything ran**, also from `start`. One bounded `ps aux` plus `vm_stat` land in
  `runs/diagnostics/ps-baseline.txt`. The stall captures snapshot `ps` at a stall. That snapshot
  answers what was resident when the runner broke. It cannot answer whether that state differed from
  normal. The first collection had to settle that question from a launch-env argument instead of a
  measurement. Each job writes both this baseline and the render probe once. A second `start` in the
  same job thus never overwrites the pre-run reading with a mid-job one.
- **Live, about each synthesized touch**, also from `start` and kept on green jobs.
  [`scripts/synthesis_stream_record.py`](../scripts/synthesis_stream_record.py) streams the guest's
  `log stream` for three sources: the runner's `bajutsu.runner` markers, `backboardd`, and
  `testmanagerd`. The recorder writes each event to `runs/diagnostics/synthesis-stream.ndjson` with
  the time the event reached the machine. `collect` stops the recorder.
  - A stream can end mid-job, for example when a lane reboots the device. The recorder then restarts
    it. `meta` records bracket each stretch of the stream. An empty or cut-short recording then
    surfaces as a warning in the report. It no longer reads as a quiet run.
  - A `log show` extract taken after the job carries no arrival time. It cannot say how long a
    marker took to reach the machine. Nor can it say whether a `backboardd` line arrived behind a
    later-stamped runner line.
  - Those two numbers are what the dropped-touch detector of
    [BE-0462](../roadmaps/BE-0462-ios-dropped-touch-retry/BE-0462-ios-dropped-touch-retry.md)
    needs. Download the artifacts and run
    [`scripts/synthesis_stream_report.py`](../scripts/synthesis_stream_report.py) over them. The
    report gives the arrival-lag percentiles and the arrival-order inversions. It also attributes
    every drop line to the touch window that contains it. The recorder observes and decides nothing.

We split the unified-log extract in two on purpose. The opposite is the natural assumption, so the
reason needs stating. Three guest processes of the Simulator serve screenshots:
`SpringBoard`, `backboardd`, and `testmanagerd`. Those processes do **not** write to the unified log of the machine.
We measured this on a booted device. A `log show` on the machine, filtered on those process names,
returns its header and nothing else. The guest's entries live in the device's own log store. The
action thus runs a second `log show` inside the guest through `simctl spawn`. The extract on the
machine stays for the CoreSimulator service processes that genuinely log there. A single extract on
the machine would have produced an empty file. That file would repeat the empty-by-construction
artifact this collection replaced.

#### The Android lane

Every job that boots an Android Virtual Device (AVD) collects the same three layers. `uiautomator
(codegen)` is no exception. That job is where the collection matters most, not least. Gradle
*running the generated test* writes every path the job uploads. Something can go wrong before that
point:

- a wedged AVD boot
- a codegen or build error
- the job's own `timeout-minutes`

In each of those cases, `if-no-files-found: ignore` used to drop the artifact without a word. A job
that went red outside its own test uploaded nothing at all. Unlike the six `bajutsu run` jobs,
`uiautomator (codegen)` runs no adb driver at test time. The resident-read trigger thus cannot go
off there. Its Layer 1 trigger is the recorder on the machine,
[`screenrecord.py`](../demos/showcase/android/screenrecord.py). That recorder confirms that its
recording is producing bytes. A wedged renderer, the known flake of this lane, produces none.

- **Inside `bajutsu`.** The stall capture triggers at two moments, both narrow on purpose.
  - The first moment is the hierarchy-read fallback of the resident channel. There the driver gives
    up on the channel. It falls back to the `uiautomator dump` subprocess for the rest of the lease.
    At that point the read channel has gone, rather than turned noisy for a moment.
  - The second moment is a check on the device-side `screenrecord`. The check asks whether the recorder
    produces bytes, not merely runs. That check tells a wedged renderer apart from a recording that never started.
  - Either moment writes a capture into `runs/diagnostics/stalls/stall-NN-<reason>-<pid>/`. The
    capture holds a `ps` and `top` snapshot of the machine. It also holds `dumpsys SurfaceFlinger
    --latency` and a `logcat` tail.
  - The capture leaves out the actuation path on purpose. Two of the resident channel's own errors
    occur during healthy runs. One comes from an older server that has no actuation endpoint. The
    other is a lost response, which the driver treats as landed rather than actuating twice.
    Capturing there would spend the per-trigger budget before a genuine stall could use it.
- **From CI, about the emulator**, through
  [`scripts/collect_android_diagnostics.sh`](../scripts/collect_android_diagnostics.sh). This half
  is a script rather than a composite action. The constraint behind that choice is the sharpest way
  the Android lane diverges from the iOS one.
  - Every device-touching step in the lane runs *inside* one step. That step uses
    `reactivecircus/android-emulator-runner`. That step boots the AVD and runs its `script:`. It then shuts the AVD down when the step
    ends.
  - An ordinary step placed after it would run with no device attached. A step-level `if: failure()`
    gate would do the same. Either would collect nothing at all.
  - The sweep thus runs from the tail of each job's own `script:`. It keys off the exit code of the
    run command. The poller `poll_cpuinfo` of the lane already uses the same shape to outlast a red
    run.
  - Its cheap tier runs on every job. That tier takes a full-buffer `logcat -d` dump. The dump reads
    the retained ring buffer of the device. The per-scenario `deviceLog` interval evidence does not. It
    thus still has something to show for a job that went red before any scenario's capture began.
  - The cheap tier also takes `dumpsys meminfo`, `getprop`, and `adb devices -l`. That snapshot
    keys a red run against an API level and an Application Binary Interface (ABI).
  - Its heavy tier runs when the run itself did not pass, and not otherwise. That tier runs `adb
    bugreport`, Android's own comprehensive collector. It also makes a rooted pull of two
    directories. `/data/tombstones` holds the native reports of abnormal exits. `/data/anr/` holds Application Not
    Responding (ANR) traces.
  - The iOS sweep gets those two classes of report for free from the machine's diagnostic
    reports. On Android they live on the device.
  - The sweep bounds itself as a whole, as well as each command. The in-process capture carries a
    budget above its per-probe timeouts for the same reason. The sweep exists to document a wedged
    device. On such a device, every `adb` read stalls to its full ceiling. Per-command ceilings
    alone would let the collection spend most of a job's `timeout-minutes`. A reportable red run
    would then become a cancelled job holding a truncated collection.
  - A read that the deadline cuts off says so in its own file. "The budget ran out" and "this read
    found nothing" are different answers about the device.
- **Over time, about the machine**, from the
  [`collect-android-diagnostics`](../.github/actions/collect-android-diagnostics/action.yml)
  composite action.
  - Its `start` phase runs before each job's emulator step. The phase launches a background
    sampler. About every 20 seconds, the sampler appends `top` and `free` output to
    `runs/diagnostics/host-telemetry.log`.
  - Its `collect` phase runs after the emulator step and stops the sampler.
  - The layer reads the Linux runner alone, which lets it be a step at all. It brackets the emulator
    step from outside. It thus covers a job whose emulator step went down outright.

The sampler on the machine coexists with the lane's `poll_cpuinfo` input. It does not replace
that input. The two measure different quantities from different sides. `poll_cpuinfo` reads the
guest's view over adb. The sampler reads the load of the machine itself. That difference also
explains why the sampler is always on while `poll_cpuinfo` is opt-in. A sample on the machine every
20 seconds issues no adb traffic and samples a tenth as often. It lacks the observer effect that
keeps `poll_cpuinfo` off by default.

The two lanes keep separate composite actions on purpose. `collect-ios-diagnostics` reads `simctl`,
CoreSimulator, and the macOS unified log. `collect-android-diagnostics` reads the Linux machine. The
underlying tools share no surface. A single action would then be a branch on the backend rather
than shared logic. The two lanes do share the design. They share the cheap/heavy split and the
`start` / `collect` telemetry phases. Inside `bajutsu`, they also share the capture itself.

**Reading what a red lane collected.** None of this collection appears in `gh run view
--log-failed`. The collection travels inside the `runs/` artifact of the red job. Diagnosing from the
log alone thus never reveals it. The
[`investigate-ci-failure`](../.apm/skills/investigate-ci-failure/SKILL.md) agent skill downloads
that artifact. The skill compares the artifact's contents with the symptoms in
[`known-ci-failure-patterns.md`](../.apm/skills/investigate-ci-failure/references/known-ci-failure-patterns.md).
It classifies the red run as one of three kinds:

- a known infrastructure flake
- a real defect
- a case still unclassified

[`pr-followup`](../.apm/skills/pr-followup/SKILL.md) runs the skill before touching a fix. A flake
of the machine then gets no code change, and a regression does not pass as a flake. The skill
reports. It never edits, pushes, or re-runs.

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
| `channel-timeout` | a runner-channel retry or a mid-run channel loss in the job log |
| `video-start` | an unconfirmed recording start in the log, a zero-byte render-probe recording, or a `video-no-bytes` or `screenrecord-no-growth` stall capture |
| `network-or-deps` | a network error in the log, or a failing step that merely sets up, installs, or downloads |
| `runner-contention` | a job that queued longer than 300 seconds |
| `app-wait-timeout` | a failing `wait` step with none of the signals above |
| `test-assertion` | any other failing step with none of the signals above |

The last two labels are residual. A red job with no infrastructure signal is where a real defect or a
too-tight wait shows up. A fetched red job whose log the fetch could not read gets
`log-signals-unknown` instead. The fetch never checked the log signals above for that job. Time of day and runner image are not labels. The report compares them as
dimensions instead.

**Running the analysis.** These steps run on a workstation. The GitHub command-line tool `gh` there
needs access to the repository.

1. Fetch the recent runs of one workflow. The command reads the last 30 completed runs and downloads
   their telemetry artifacts. It joins each record with facts from the API. Those facts are the job's
   conclusion, its queue time, and each step's duration. The command also counts six warnings in
   each job log:

   - channel retries
   - channel losses
   - unconfirmed recording starts
   - Simulator reboots that recovered a runner start
   - replaced Simulators
   - network errors

   ```bash
   python3 scripts/ci_telemetry.py fetch --workflow ios-e2e.yml --limit 30 --out tmp/ci-telemetry
   ```

   The command prints `N job record(s) → tmp/ci-telemetry/records.jsonl`. A job without a telemetry
   artifact still gets a record from the API alone. A job that stopped before its `collect` step
   thus stays in the count. The command skips a cancelled job. The E2E workflows cancel a
   superseded run when a newer push arrives. Such a run says nothing about the code or the machine.
   GitHub also reports a job that its `timeout-minutes` stopped as cancelled. The command tells the
   two apart by the timeout annotation on the job. It counts a job stopped that way as red.

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

### Deciding whether to keep the reduced Simulator services

The `run` and `actuation` jobs turn off Simulator services that no scenario needs
([BE-0517](../roadmaps/BE-0517-ios-ci-simulator-service-reduction/BE-0517-ios-ci-simulator-service-reduction.md)).
The roadmap item fixes four criteria for keeping the change, before any measured run. The
`adoption` subcommand applies those criteria to two arms of manual runs:

- **reduced**: the Simulator with the listed services turned off
- **stock**: the same reboot with every service left on

1. Start ten runs of each arm on `main`. Alternate the arms within the same hours. Note the time of
   the first run, since the report counts nothing older.

   ```bash
   gh workflow run ios-e2e.yml --ref main -f lanes=measure -f simulator-services=reduced
   ```

   ```bash
   gh workflow run ios-e2e.yml --ref main -f lanes=measure -f simulator-services=stock
   ```

2. Fetch the runs once all twenty have completed. The `--event` flag keeps pull-request runs of the
   same workflow out of the list.

   ```bash
   python3 scripts/ci_telemetry.py fetch --workflow ios-e2e.yml --event workflow_dispatch --limit 30 --out tmp/be-0517
   ```

3. Apply the criteria.

   ```bash
   python3 scripts/ci_telemetry.py adoption tmp/be-0517 --since 2026-12-01T00:00:00Z
   ```

   The first line names the verdict. The table below it gives each arm's counts and each
   criterion's result.

The subcommand assigns a job to an arm by the mode its own log printed. A job counts when it meets
three conditions:

- The job is `run` or `actuation`, from a manual run on `main`.
- It started at or after the `--since` time.
- The fetch read its log.

The verdict takes one of four values.

| Verdict | Meaning |
|---|---|
| `adopt` | Every criterion passes |
| `reject` | At least one criterion does not pass |
| `trace` | The counts pass, but a job did not pass with the change on |
| `incomplete` | An arm holds other than exactly twenty jobs |

A `trace` verdict needs a person. Run each listed scenario again with the suspect service left
on. A pass then counts against the change.

The report also lists each `run` or `actuation` job whose log names no arm. The fetch could not
read its log, or the job stopped before the reduce step printed the arm. Such a job counts in
neither arm, and an arm may then fall short of twenty.

## Running bajutsu in your app's CI

> bajutsu is pre-release (unpublished). Until it is on PyPI, vendor it (a submodule or a
> checkout) and run the action from that checkout — the action runs `uv sync` against
> bajutsu's `pyproject.toml`.

bajutsu produces CI-ready output:

- a `junit.xml` file
- a self-contained `report.html`
- an exit code of `0` or `1`
- inside Actions, error **annotations** and a job **summary**, described below

On a macOS runner, follow these two steps:

1. **Build and install your app** and the XCUITest runner onto a booted Simulator. This step varies
   per app, so it stays yours. It typically uses `xcodebuild` and `xcrun simctl install`.
2. **Run bajutsu** with the [`bajutsu-e2e`](../.github/actions/bajutsu-e2e/action.yml) composite
   action. The action syncs dependencies and runs your scenarios with `run --score`. The log then
   carries the grade of the entry-screen convention. The action uploads the run as an artifact. That
   artifact holds the report, the screenshots, the video, and `network.json`. The XCUITest backend
   needs no pip extra. `bajutsu` drives its runner over Hypertext
   Transfer Protocol. `xcodebuild` ships with Xcode on the runner.

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

### Error annotations + job summary

`bajutsu run` checks the `GITHUB_ACTIONS` variable. When the variable holds a value, the run
emits one `::error::` annotation per red scenario. The PR shows each annotation inline.
The command also appends a table of passing and red scenarios to `$GITHUB_STEP_SUMMARY`. No flag
turns this on. The command detects the Actions environment by itself.

### Notes

- **JUnit**: `bajutsu` writes `junit.xml` next to the report. Feed that file to a test-reporter
  action for an inline test view. One such action is `dorny/test-reporter`.
- **Determinism**: use scenario [`mocks`](network.md#deterministic-mocks) to stub the network. Runs
  then do not depend on a live server.
- **Convention score**: the composite action passes `run --score`. The run then prints the app's
  entry-screen convention grade to stderr: `Ready`, `Partial`, or `Blocked`. `doctor` reports the
  same score. The run computes the grade from its own first launch.
  - The grade serves diagnosis alone and never sits on the pass/fail path.
  - Folding the grade into the run avoids a separate `doctor` step. That step would cold-spawn a
    second XCUITest runner.
  - Run `bajutsu doctor` locally for the fuller preflight. It adds runnability and capability
    checks. A hard runnability gate in CI is future work. That gate would cover the environment,
    permissions, and the presence of `xcodebuild` and Xcode.
- **Affected-step selection**: [`bajutsu impact`](cli.md#impact) reports which scenario steps a
  `git` diff may affect. A pipeline can then order those steps first for fast feedback.
  - The safe default is additive. Run the whole suite, with the affected steps first.
  - Narrowing a pre-merge run down to the affected set is opt-in. The narrowing stays sound with
    both safeguards that `impact` documents, and with nothing less.
  - The first safeguard: fall back to the full suite whenever the report is **incomplete**. In
    `--json`, `complete` is then `false`. That value means the diff carried a change that maps to no
    referenced id.
  - The second safeguard: still run the full deterministic suite at a coarser cadence. That cadence
    can be on merge, nightly, or at release.
  - The pass/fail verdict always stays with `run`.
