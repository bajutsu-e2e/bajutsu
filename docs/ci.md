# Continuous integration

This document covers two separate topics:

1. **CI for this repo** — guard the tool itself (`.github/workflows/`).
2. **Running bajutsu in *your* app's CI** — a composite action + recipe you reuse.

## This repo's CI

| Workflow | Runner | When | What |
|---|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) (`check` job) | Linux | push to `main`, every PR (pull request) | the full `make check` gate on Python 3.13 — lockfile freshness (`uv lock --check`), formatting (`ruff format --check`), lint (`ruff`), shell lint (`shellcheck`), workflow lint (`actionlint`), types (`mypy bajutsu demos scripts`), and `pytest` with a coverage floor (`--cov-fail-under=89`). The logic layer needs no Simulator, so it is fast and cheap |
| [`web-e2e.yml`](../.github/workflows/web-e2e.yml) | Linux | manual + every PR + merge queue (required `E2E (web)` check) | the **web (Playwright) backend** lane (BE-0279), four jobs against a headless Chromium: **smoke (playwright)** runs the `demos/web` scenarios (`make -C demos/web e2e`); **dogfood (serve UI)** drives Bajutsu's own serve SPA (BE-0058); **conformance (playwright)** runs the BE-0114 driver contract against the real browser; **network (playwright)** drives the real network path — `page.route` interception, `requestfinished` capture, the `mocked` flag, and redaction of really-captured evidence (`make -C demos/web e2e-network`, BE-0282). **No Mac / Simulator**, so it proves the core is platform-neutral. All four are path-gated by the same `changes` detector (`scripts/e2e_changes.py`, `E2E_LANE=web`) and — deterministic and host-independent — all feed the always-reporting `E2E (web)` job, the required check; a PR that can't affect the web path skips the browser jobs and the gate passes. **network (playwright)** landed as a per-PR signal first and, having proven stable in CI, is now promoted into `E2E (web)`'s `needs:` (BE-0282) — the web twin of `android-e2e.yml`'s already-gating `network (adb)` |
| [`dependency-audit.yml`](../.github/workflows/dependency-audit.yml) | Linux | manual + weekly + push to `main` / PRs touching `pyproject.toml` / `uv.lock` | audit the locked dependency graph (`uv export` → `pip-audit --no-deps`) against the advisory DB. The result is a function of the lockfile and the DB, so it runs on a dependency change and on a weekly schedule that catches advisories newly disclosed against unchanged pins |
| [`swift.yml`](../.github/workflows/swift.yml) | macOS | push to `main` + PRs touching `BajutsuKit/**` | `swift build` + `swift test` for [BajutsuKit](../BajutsuKit). Unit-tests the pure-Foundation logic (request matching / mock parsing) with no Simulator — the on-device interception itself is covered by `ios-e2e.yml` |
| [`ios-e2e.yml`](../.github/workflows/ios-e2e.yml) | macOS | manual + every PR + merge queue (required `E2E` check) | the **iOS (XCUITest) backend** lane, seven jobs against the showcase, all on the XCUITest backend (the resident BajutsuRunner): **smoke (xcuitest)** builds the showcase, boots a Simulator, and runs a scenario through the XCUITest backend (driver + simctl + the resident runner + real actuation); **actuation (xcuitest)** drives real on-device XCUITest actuation beyond the conformance job's `tap` (BE-0281) — `back` (`navigation.yaml`) and device control (`setLocation` / clipboard / `push`, `device.yaml` + `push.yaml`) on the Stable launch tab (the tab-crossing gesture / text scenarios run in the `xcuitest (multi-touch)` job); **golden (xcuitest)** runs the BE-0006 element-tree golden over XCUITest (`golden.yaml`) — the iOS twin of `android-e2e.yml`'s `golden (adb)`; **xcuitest (codegen)** generates a native XCUITest from a scenario (`make -C demos/showcase ui-test`) and runs it with `xcodebuild` (no bajutsu / AI at test time); **xcuitest (multi-touch)** runs a pinch/rotate scenario through the full `bajutsu run` path on the XCUITest backend (BE-0019) — two-finger gestures — and also runs the runner channel's `/type` (`search.yaml`) and `/swipe` + `/back` (`notices.yaml`) actuation (BE-0281), tab-crossing scenarios; **conformance (xcuitest)** runs the driver conformance suite (BE-0114) on-device against the XCUITest backend; and **visual (xcuitest)** pixel-compares the Stable catalog against the committed `baselines_ios/` baseline (`make -C demos/showcase e2e-visual`), masking the status bar + the "Liquid Glass" tab bar. The smoke, xcuitest (codegen), xcuitest (multi-touch), and conformance jobs are path-gated by a `changes` detector and feed a single always-reporting `E2E` job — the required check. `actuation`, `golden`, and `visual` are gated by the same detector but deliberately excluded from `E2E`'s `needs:`: newly-wired XCUITest actuation lands as a per-PR signal first (the Simulator lane has a flakiness history, BE-0218) and is promoted to the gate only once stable, the element-tree `golden` is deterministic and host-independent so its drift surfaces as a per-PR signal, and a `visual` pixel baseline is host-specific (the Simulator renderer varies by Xcode / device / OS), so each surfaces a drift or a flake as a signal on its own job (visual's captured screenshot uploads as `ios-e2e-visual-run` to re-record the baseline from) rather than blocking a merge |
| [`android-e2e.yml`](../.github/workflows/android-e2e.yml) | Linux | manual + every PR + merge queue (required `E2E (android)` check) | the **Android (adb) backend** lane (BE-0208), six per-concern jobs mirroring the iOS and web lanes' job splits, each booting its own x86_64 API 34 AVD under KVM (`reactivecircus/android-emulator-runner`): **smoke (adb)** builds the Compose + Views showcase APKs and runs the Stable-tab scenarios — the core id/tap/type/value flows plus a push/pop back-navigation flow — through `--backend android` (`make -C demos/showcase/android e2e`); **golden (adb)** runs the on-device golden element-tree check for the Compose Stable catalog (`make -C demos/showcase/android e2e-golden`, BE-0006 / BE-0208 unit 4), then re-runs it with the resident channel forced off (`make -C demos/showcase/android e2e-fallback`, BE-0245) so both read channels are proven to agree; **conformance (adb)** runs the driver conformance suite (BE-0114) against the real adb backend — the Android twin of `ios-e2e.yml`'s `conformance (xcuitest)`; **visual (adb)** runs the pixel VRT (below). **No Mac / Simulator** — the third backend's Linux twin of the iOS and web e2e lanes. Path-gated by a `changes` detector (`scripts/e2e_changes.py`, `E2E_LANE=android`), feeding the required `E2E (android)` aggregator (BE-0279). The AVD is x86_64 (not the local validation's arm64) so KVM accelerates it on the x86_64 runner; the golden's baseline is recorded on arm64 yet passes on x86_64 because the comparison is field-level with a tolerant frame check. The sheet/cover flows (`components`, `modals`) are included by raising the condition-wait ceiling for this lane only — `make -C demos/showcase/android e2e` exports `BAJUTSU_MIN_WAIT_TIMEOUT` (default 15s), a floor under each wait's own timeout — because the software-rendered emulator draws a modal slower than the shared scenarios' 5s waits allow. A condition wait returns the instant it is satisfied, so the larger ceiling is a safe upper bound, not a fixed delay, and the shared scenarios stay untouched (their `timeout: 5` is the same on every backend). The deep-scroll flows (`controls`, `notices`) join the lane too: the default directional swipe now travels a fraction of the screen rather than a fixed coordinate count, so a swipe reaches the same proportion of Android's dense screen (2400px) as of iOS's (~900pt) — a fixed count scrolled far less on Android and never brought the far target (the segmented-control value node, a bottom list row) into view (`bajutsu/orchestrator/actions/handlers/gestures.py`, BE-0208 unit 5). The single-touch gesture flow (`gestures`) joins too: the adb driver drives a double-tap with a raw `sendevent` touch sequence on a rooted emulator (the `e2e` target runs `adb root` first), firing both taps inside the platform double-tap window that a per-tap `input` JVM overran; on a non-rooted device it falls back to `input tap` unchanged (BE-0208 unit 5). The multi-touch gesture flow (`gestures_multitouch`) joins too (BE-0232): the adb driver drives a pinch / rotate as a raw two-slot `sendevent` sweep on the rooted emulator — two contacts moving together across interleaved frames — so the shared scenario that iOS runs on XCUITest runs unchanged on Android; unlike the single-touch double-tap there is no `input` fallback (a two-finger gesture cannot be approximated), so it requires root and fails loudly otherwise. The runtime-permission flow (`permission`) joins the lane too (BE-0208 unit 6, exercising BE-0210's up-front grant): it is the **same** `permission.yaml` the iOS lane runs — the grant mechanism lives in config, not the scenario, so one file serves both. `showcase-compose` grants `POST_NOTIFICATIONS` up front (`grantPermissions` → `pm grant` at lease time), so Android's `RequestPermission` contract short-circuits to granted with no dialog; the scenario's `dismissAlerts` guard therefore never fires here, keeping the flow deterministic (no LLM, no fixed sleep) on the lane (on iOS, where notifications can't be pre-granted, that guard taps "Allow" instead). The device-control flow (`device`) joins the lane too (BE-0208 unit 5): it overrides the GPS location (`emu geo fix`), round-trips the clipboard, and re-asserts the settled screen — the **same** `device.yaml` the iOS lane runs (unified across iOS/Android, since `setLocation` and the clipboard are advertised on both; the iOS-only `push` half lives in `push.yaml`), run on the Stable launch tab. Both `setLocation` and `clipboard` of the device-control family are exercised: `cmd clipboard` is a silent no-op on-device and since Android 10 only the foreground app may touch the clipboard, so the clipboard runs through an in-app receiver the showcase embeds from `BajutsuAndroid` (BE-0233) — the seed/read-back is the strong assertion PR #934 wanted. The `visual (adb)` job runs a pixel visual-regression check for the Compose Stable catalog (`make -C demos/showcase/android e2e-visual`, BE-0208 unit 4): unlike the element-tree golden, a pixel baseline is host-specific — the x86_64 software renderer (swiftshader) and a local arm64 emulator diverge per pixel — so this baseline is recorded on this x86_64 lane and committed (`demos/showcase/scenarios/visual/baselines_android/`), not on arm64; the top status bar is masked so the wall clock never churns the comparison. The `uiautomator (codegen)` job is the codegen output path (`make -C demos/showcase/android e2e-codegen`, BE-0294) — the Android twin of `ios-e2e.yml`'s `xcuitest (codegen)`: it re-generates a native UI Automator (Kotlin) test from `codegen_android.yaml`, then Gradle's `connectedAndroidTest` builds the Compose a11y app + instrumentation APKs, installs both, and runs the generated test against the emulator with no bajutsu / adb driver / AI at test time; regenerating before the build means a stale check-in cannot mask an emitter or `androidx.test.uiautomator` API drift. The deterministic host-independent jobs — `smoke (adb)` and `conformance (adb)` — feed the always-reporting `E2E (android)` aggregator, the required check (BE-0279); `golden (adb)`, `visual (adb)`, and `uiautomator (codegen)` are deliberately excluded from its `needs:` and stay per-PR signals (the element-tree golden can drift with an upstream dependency, a pixel baseline is host-specific, and the codegen lane lands as a signal first per the BE-0282 precedent), the same gate boundary iOS's `E2E` draws |
| [`devicefarm.yml`](../.github/workflows/devicefarm.yml) | Linux | **manual only** (`workflow_dispatch`) | the **AWS Device Farm batch submit** (BE-0235) — builds the showcase Compose APK, packages Bajutsu + config + scenarios, and hands them to [`scripts/devicefarm_submit.py`](../scripts/devicefarm_submit.py), which uploads a custom-environment test spec that runs `bajutsu run --backend adb` on Device Farm's host, polls the run, downloads the artifacts, and surfaces **Bajutsu's own manifest verdict** (never Device Farm's classification). It is CI-side glue outside the deterministic core, so no LLM touches the verdict; it is `workflow_dispatch` only (never on push/PR, not a required check). Auth is a short-lived AWS credential from GitHub OIDC (`AWS_DEVICEFARM_ROLE_ARN`) scoped to a `devicefarm` Environment, with the project / device-pool ARNs in repository variables; with any unset the job is a green no-op, so it stays dormant until an operator wires up an account. The real-account serial-resolution PoC is a documented human procedure (see [AWS Device Farm](devicefarm.md)), deliberately kept off the deterministic gate |

### Which E2E checks gate a merge (BE-0279)

Each backend lane — iOS (`E2E`), Android (`E2E (android)`), and web (`E2E (web)`) — carries one
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
with `E2E_LANE=ios|android|web` (the per-lane positive-list of relevant paths, unit-tested in
`tests/test_e2e_changes.py`). The aggregator runs `if: always()`, so a path-skip reports as a pass and
an unrelated PR is neither run nor blocked. Adding a new required aggregator to `main`'s
branch-protection ruleset is an out-of-repo administrative step, done by a maintainer with the exact
check name.

The dev tools live in the `dev` dependency group, so the Linux job runs `uv sync --group
dev` then `uv run --no-sync …` (plain `uv run` would re-sync to the default set and drop
them). The gate mirrors [`make check`](../Makefile) and the [`pre-push`](../.githooks/pre-push)
hook step-for-step; every check except `actionlint` (a standalone binary CI installs) runs
identically on a fresh clone via `uv` alone, which is what makes "green locally" predict
"green in CI".

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
   composite action — it syncs deps, runs an optional `doctor`
   preflight, runs your scenarios, and uploads the run (report + screenshots + video +
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
- **`doctor`**: today it is a convention *score* (non-blocking preflight); a hard
  env/permission runnability gate (`xcodebuild` / Xcode presence) is future work.
