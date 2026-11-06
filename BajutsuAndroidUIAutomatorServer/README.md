**English** · [日本語](README.ja.md)

# BajutsuAndroidUIAutomatorServer — resident UI Automator server

A self-contained UI Automator instrumentation that bajutsu installs on the target device to answer
screen reads over a local socket, keeping one `UiAutomation` session alive for the whole run. It
exists to remove the per-invocation startup cost of `adb exec-out uiautomator dump` (≈ 2.4 s each,
independent of tree size) that makes the Android adb backend read an order of magnitude slower than
iOS. See the roadmap item
[BE-0245](../roadmaps/BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md).

## How it differs from BajutsuAndroid

[`BajutsuAndroid`](../BajutsuAndroid/) is an **app-embedded** library (the Android peer of
BajutsuKit): an app under test includes it by Gradle path to give bajutsu an in-app foothold for
capabilities the platform only exposes from the app process, such as the clipboard (BE-0233).

This server is the opposite: an **app-independent** instrumentation that bajutsu installs and
drives itself, the way Appium's UiAutomator2 server works. It has no UI, embeds into no app, and
reads whatever app is on screen. That is why it lives in its own Gradle project rather than being
included into an app's build.

## Shape

- The server is an `androidTest` instrumentation. `am instrument -w` runs a single blocking `@Test`
  (`ResidentServerTest.serve`) that never returns — it opens a socket and serves until the
  instrumentation is killed, which is what keeps the `UiAutomation` session warm.
- Transport is a hand-rolled HTTP/1.1 over a raw `ServerSocket` bound to loopback (port 6790), with
  no HTTP or JSON dependency: the server answers a small, fixed set of paths.
- `GET /source` returns `UiDevice.dumpWindowHierarchy()`'s XML, the same
  `AccessibilityNodeInfoDumper` format bajutsu's `parse_hierarchy` already parses.
- `POST /type` enters text that `adb shell input text` cannot carry into the field holding input
  focus. The text arrives as a base64 request body, and the server applies it with `ACTION_SET_TEXT`.

One window difference is reconciled on the Python side: `dumpWindowHierarchy()` traverses every
window, so its XML also carries the SystemUI status bar (clock, wifi, battery, notification icons —
29 nodes) that the platform `uiautomator dump` omits by scoping to the active window. The app content
is identical; `bajutsu.common.backend_cli.adb_resident.narrowed_root` drops the SystemUI decor windows so both
paths yield the same Elements (the equivalence unit 2 of the roadmap item requires).

The Python side that reaches this socket — `adb forward`, the `fetch_hierarchy` wiring, and the
lifecycle tied to the device lease — lives in [`bajutsu/common/backend_cli/adb_resident.py`](../bajutsu/common/backend_cli/adb_resident.py)
and is wired into the Android lease in `bajutsu/platform_lifecycle.py` (BE-0245 PR-C). It is opt-in
behind the `BAJUTSU_ADB_RESIDENT` environment variable until the Android e2e lane builds and installs
the server; unset, the adb backend reads via `uiautomator dump` exactly as before.

## Build

```bash
make -C BajutsuAndroidUIAutomatorServer build   # host APK + instrumentation (androidTest) APK
```

This is not part of `make check` (the Python gate builds no Kotlin). It goes through the committed
`./gradlew`, so a fresh clone needs no system-installed Gradle — only the Android SDK.

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
