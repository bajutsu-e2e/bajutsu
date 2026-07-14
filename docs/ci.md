# Continuous integration

This covers two separate topics:

1. **CI for this repo** — guard the tool itself (`.github/workflows/`).
2. **Running bajutsu in *your* app's CI** — a composite action + recipe you reuse.

## This repo's CI

| Workflow | Runner | When | What |
|---|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) (`check` job) | Linux | push to `main`, every PR (pull request) | the full `make check` gate on Python 3.13 — lockfile freshness (`uv lock --check`), formatting (`ruff format --check`), lint (`ruff`), shell lint (`shellcheck`), workflow lint (`actionlint`), types (`mypy bajutsu demos scripts`), and `pytest` with a coverage floor (`--cov-fail-under=89`). The logic layer needs no Simulator, so it's fast and cheap |
| [`web-e2e.yml`](../.github/workflows/web-e2e.yml) | Linux | manual + push to `main` / PRs touching the core run path, the web backend, the web demo, or deps | the **web (Playwright) backend** smoke — `playwright install chromium`, then `make -C demos/web e2e` runs the `demos/web` scenarios deterministically against a browser. **No Mac / Simulator**, so it proves the core is platform-neutral. Installing Chromium + running a browser is heavy, so it's path-gated (mirroring `ios-e2e.yml`'s relevance rule) rather than run on every PR |
| [`dependency-audit.yml`](../.github/workflows/dependency-audit.yml) | Linux | manual + weekly + push to `main` / PRs touching `pyproject.toml` / `uv.lock` | audit the locked dependency graph (`uv export` → `pip-audit --no-deps`) against the advisory DB. The result is a function of the lockfile and the DB, so it runs on a dependency change and on a weekly schedule that catches advisories newly disclosed against unchanged pins |
| [`swift.yml`](../.github/workflows/swift.yml) | macOS | push to `main` + PRs touching `BajutsuKit/**` | `swift build` + `swift test` for [BajutsuKit](../BajutsuKit). Unit-tests the pure-Foundation logic (request matching / mock parsing) with no Simulator — the on-device interception itself is covered by `ios-e2e.yml` |
| [`ios-e2e.yml`](../.github/workflows/ios-e2e.yml) | macOS | manual + every PR + merge queue (required `E2E` check) | the **iOS (idb / XCUITest) backend** lane, five jobs against the showcase: **smoke (idb)** builds the showcase, boots a Simulator, and runs a scenario through the idb backend (driver + simctl + idb); **xcuitest (codegen)** generates a native XCUITest from a scenario (`make -C demos/showcase ui-test`) and runs it with `xcodebuild` (no bajutsu / idb / AI at test time); **xcuitest (multi-touch)** runs a pinch/rotate scenario through the full `bajutsu run` path on the XCUITest backend (BE-0019) — two-finger gestures idb cannot actuate; **conformance (idb + xcuitest)** runs the driver conformance suite (BE-0114) on-device against both backends; and **visual (idb)** pixel-compares the Stable catalog against the committed `baselines_ios/` baseline (`make -C demos/showcase e2e-visual`), masking the status bar + the "Liquid Glass" tab bar. The first four jobs are path-gated by a `changes` detector and feed a single always-reporting `E2E` job — the required check. `visual` is gated by the same detector but deliberately excluded from `E2E`'s `needs:`: a pixel baseline is host-specific (the Simulator renderer varies by Xcode / device / OS), so a drift surfaces as a signal on its own job (the captured screenshot uploads as `ios-e2e-visual-run` to re-record the baseline from) rather than blocking a merge (the committed element-tree golden still runs on the weekly idb-monitor) |
| [`android-e2e.yml`](../.github/workflows/android-e2e.yml) | Linux | manual + push to `main` / PRs touching the adb backend, the Android showcase, the app-side SDK (`BajutsuAndroid`), the shared scenarios, or deps | the **Android (adb) backend** smoke (BE-0208) — boots an x86_64 API 34 AVD under KVM (`reactivecircus/android-emulator-runner`), builds the Compose showcase APK, and runs the Stable-tab scenarios — the core id/tap/type/value flows plus a push/pop back-navigation flow — through `--backend android` (`make -C demos/showcase/android e2e`), then an on-device golden element-tree check for the Compose Stable catalog (`make -C demos/showcase/android e2e-golden`, BE-0006 / BE-0208 unit 4) in the same emulator session. **No Mac / Simulator** — the third backend's Linux twin of the idb and web e2e lanes. Path-gated like `web-e2e.yml`, not a required check. The AVD is x86_64 (not the local validation's arm64) so KVM accelerates it on the x86_64 runner; the golden's baseline is recorded on arm64 yet passes on x86_64 because the comparison is field-level with a tolerant frame check. The sheet/cover flows (`components`, `modals`) are included by raising the condition-wait ceiling for this lane only — `make -C demos/showcase/android e2e` exports `BAJUTSU_MIN_WAIT_TIMEOUT` (default 15s), a floor under each wait's own timeout — because the software-rendered emulator draws a modal slower than the shared scenarios' 5s waits allow. A condition wait returns the instant it is satisfied, so the larger ceiling is a safe upper bound, not a fixed delay, and the shared scenarios stay untouched (their `timeout: 5` is the same on every backend). The deep-scroll flows (`controls`, `notices`) join the lane too: the default directional swipe now travels a fraction of the screen rather than a fixed coordinate count, so a swipe reaches the same proportion of Android's dense screen (2400px) as of iOS's (~900pt) — a fixed count scrolled far less on Android and never brought the far target (the segmented-control value node, a bottom list row) into view (`bajutsu/orchestrator/actions/handlers/gestures.py`, BE-0208 unit 5). The single-touch gesture flow (`gestures`) joins too: the adb driver drives a double-tap with a raw `sendevent` touch sequence on a rooted emulator (the `e2e` target runs `adb root` first), firing both taps inside the platform double-tap window that a per-tap `input` JVM overran; on a non-rooted device it falls back to `input tap` unchanged (BE-0208 unit 5). The multi-touch gesture flow (`gestures_multitouch`) joins too (BE-0232): the adb driver drives a pinch / rotate as a raw two-slot `sendevent` sweep on the rooted emulator — two contacts moving together across interleaved frames — so the shared scenario that iOS runs on XCUITest runs unchanged on Android; unlike the single-touch double-tap there is no `input` fallback (a two-finger gesture cannot be approximated), so it requires root and fails loudly otherwise. The runtime-permission flow (`permission`) joins the lane too (BE-0208 unit 6, exercising BE-0210's up-front grant): it is the **same** `permission.yaml` the idb lane runs — the grant mechanism lives in config, not the scenario, so one file serves both. `showcase-compose` grants `POST_NOTIFICATIONS` up front (`grantPermissions` → `pm grant` at lease time), so Android's `RequestPermission` contract short-circuits to granted with no dialog; the scenario's `dismissAlerts` guard therefore never fires here, keeping the flow deterministic (no LLM, no fixed sleep) on the lane (on iOS, where notifications can't be pre-granted, that guard taps "Allow" instead). The device-control flow (`device`) joins the lane too (BE-0208 unit 5): it overrides the GPS location (`emu geo fix`), round-trips the clipboard, and re-asserts the settled screen — the **same** `device.yaml` the idb lane runs (unified across iOS/Android, since `setLocation` and the clipboard are advertised on both; the iOS-only `push` half lives in `push.yaml`), run on the Stable launch tab. Both `setLocation` and `clipboard` of the device-control family are exercised: `cmd clipboard` is a silent no-op on-device and since Android 10 only the foreground app may touch the clipboard, so the clipboard runs through an in-app receiver the showcase embeds from `BajutsuAndroid` (BE-0233) — the seed/read-back is the strong assertion PR #934 wanted. Finally a pixel visual-regression check for the Compose Stable catalog runs in the same session (`make -C demos/showcase/android e2e-visual`, BE-0208 unit 4): unlike the element-tree golden, a pixel baseline is host-specific — the x86_64 software renderer (swiftshader) and a local arm64 emulator diverge per pixel — so this baseline is recorded on this x86_64 lane and committed (`demos/showcase/scenarios/visual/baselines_android/`), not on arm64; the top status bar is masked so the wall clock never churns the comparison |

The dev tools live in the `dev` dependency group, so the Linux job runs `uv sync --group
dev` then `uv run --no-sync …` (plain `uv run` would re-sync to the default set and drop
them). The gate mirrors [`make check`](../Makefile) and the [`pre-push`](../.githooks/pre-push)
hook step-for-step; every check except `actionlint` (a standalone binary CI installs) runs
identically on a fresh clone via `uv` alone, which is what makes "green locally" predict
"green in CI".

## Running bajutsu in your app's CI

> bajutsu is pre-release (unpublished). Until it's on PyPI, vendor it (a submodule or a
> checkout) and run the action from that checkout — the action runs `uv sync` against
> bajutsu's `pyproject.toml`.

bajutsu produces CI-ready output: a `junit.xml`, a self-contained `report.html`,
a `0` / `1` exit code, and — inside Actions — failure **annotations** + a job **summary**
(see below). On a macOS runner:

1. **Build and install your app** onto a booted Simulator (this varies per app, so it
   stays yours — `xcodebuild` + `xcrun simctl install`).
2. **Run bajutsu** with the [`bajutsu-e2e`](../.github/actions/bajutsu-e2e/action.yml)
   composite action — it installs `idb_companion`, syncs deps, runs an optional `doctor`
   preflight, runs your scenarios, and uploads the run (report + screenshots + video +
   `network.json`) as an artifact.

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
  network so runs don't depend on a live server.
- **`doctor`**: today it is a convention *score* (non-blocking preflight); a hard
  env/permission runnability gate (idb / idb_companion / Xcode presence) is future work.
