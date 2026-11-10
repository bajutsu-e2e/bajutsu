**English** · [日本語](README.ja.md)

# BajutsuAndroidUIAutomatorServer — resident UI Automator server

BajutsuAndroidUIAutomatorServer is a self-contained UI Automator instrumentation. bajutsu installs it on
the target device to answer screen reads over a local socket, and it keeps one `UiAutomation` session
alive for the whole run.

The server exists to remove the startup cost that `adb exec-out uiautomator dump` pays on every
invocation (≈ 2.4 s each, independent of tree size). That cost makes the Android adb backend read an
order of magnitude slower than iOS. See the roadmap item
[BE-0245](../roadmaps/BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md).

## How it differs from BajutsuAndroid

[`BajutsuAndroid`](../BajutsuAndroid/) is an **app-embedded** library, the Android peer of BajutsuKit.
An app under test includes it by Gradle path. The library gives bajutsu an in-app foothold for
capabilities the platform exposes only from the app process, such as the clipboard (BE-0233).

This server is the opposite: an **app-independent** instrumentation that bajutsu installs and drives
itself, the way Appium's UiAutomator2 server works. It has no UI, embeds into no app, and reads
whatever app is on screen. For that reason it lives in its own Gradle project, not in an app's build.

## Shape

- The server is an `androidTest` instrumentation. `am instrument -w` runs a single blocking `@Test`
  (`ResidentServerTest.serve`) that never returns. The test opens a socket and serves until the
  instrumentation is killed, which keeps the `UiAutomation` session warm.
- The transport is a hand-rolled HTTP/1.1 over a raw `ServerSocket` bound to loopback (port 6790), with
  no HTTP or JSON dependency. The server answers a small, fixed set of paths.
- `GET /source` returns the XML of `UiDevice.dumpWindowHierarchy()`. The XML uses the same
  `AccessibilityNodeInfoDumper` format that bajutsu's `parse_hierarchy` already parses.
- `POST /type` enters text into the field that holds input focus, for text that
  `adb shell input text` cannot carry. The text arrives as a base64 request body, and the server
  applies it with `ACTION_SET_TEXT`.

### Window difference reconciled in Python

`dumpWindowHierarchy()` traverses every window. Its XML therefore also carries the SystemUI status bar
(clock, wifi, battery, notification icons — 29 nodes), which the platform `uiautomator dump` omits by
scoping to the active window. The app content is identical.

`bajutsu.common.backend_cli.adb_resident.narrowed_root` drops the SystemUI decor windows, so both paths
yield the same Elements. Unit 2 of the roadmap item requires this equivalence.

### Python side

The Python side reaches this socket through three pieces: `adb forward`, the `fetch_hierarchy` wiring,
and a lifecycle tied to the device lease. That code lives in
[`bajutsu/common/backend_cli/adb_resident.py`](../bajutsu/common/backend_cli/adb_resident.py), and
`bajutsu/platform_lifecycle.py` wires it into the Android lease (BE-0245 PR-C).

The feature is opt-in behind the `BAJUTSU_ADB_RESIDENT` environment variable until the Android e2e
lane builds and installs the server. When the variable is unset, the adb backend reads via
`uiautomator dump` exactly as before.

## Build

```bash
make -C BajutsuAndroidUIAutomatorServer build   # host APK + instrumentation (androidTest) APK
```

The build is not part of `make check`, because the Python gate builds no Kotlin. It goes through the
committed `./gradlew`, so a fresh clone needs no system-installed Gradle, only the Android SDK.

## Measuring a slow read

These steps measure one `GET /source` on a screen that never goes idle, and they show where its time
went. The worst case is a screen that publishes accessibility events more often than every 500 ms,
because `UiDevice.waitForIdle()` then waits for its full bound. Before the wait budget existed, one
read on this screen took 120.9 seconds on an API 34 emulator.

You need a built server, its two APKs installed, and one booted emulator or device. Bajutsu installs
the APKs itself during a run, so you can skip the install after any Android run.

1. Open the Clock app's Stopwatch tab, and start the stopwatch. The display updates about ten times
   per second.

2. Start the server. The command stays attached, so run it in its own terminal.

   ```bash
   adb shell am instrument -w -e class dev.bajutsu.android.server.ResidentServerTest#serve dev.bajutsu.android.server.test/androidx.test.runner.AndroidJUnitRunner
   ```

3. Forward the server's port.

   ```bash
   adb forward tcp:6790 tcp:6790
   ```

4. Time one read. An answer of `200` in about 3.6 seconds shows the budget holding, under the
   host's 5-second read timeout.

   ```bash
   curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' 'http://127.0.0.1:6790/source?budgetMs=3500'
   ```

5. Read where the time went. The `source waits` line lists each stage.

   ```bash
   adb logcat -d -s BajutsuResidentServer:D
   ```

   ```text
   source waits: idle=1074ms dump=2140ms idle=330ms dump=25ms total=3570ms budget=3500ms
   ```

6. Clean up.

   ```bash
   adb forward --remove tcp:6790
   ```

   ```bash
   adb shell am force-stop dev.bajutsu.android.server
   ```

`tests/test_adb_resident_budgets.py` keeps these waits inside the host's timeouts on every `make check`,
with no device. The numbers themselves live in
[`bajutsu/common/backend_cli/adb_resident/budgets.py`](../bajutsu/common/backend_cli/adb_resident/budgets.py).
