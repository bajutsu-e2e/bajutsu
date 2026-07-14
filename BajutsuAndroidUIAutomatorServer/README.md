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
  no HTTP or JSON dependency: the server answers exactly one verb on one path.
- `GET /source` returns `UiDevice.dumpWindowHierarchy()`'s XML, the same
  `AccessibilityNodeInfoDumper` format bajutsu's `parse_hierarchy` already parses.

One window difference is reconciled on the Python side: `dumpWindowHierarchy()` traverses every
window, so its XML also carries the SystemUI status bar (clock, wifi, battery, notification icons —
29 nodes) that the platform `uiautomator dump` omits by scoping to the active window. The app content
is identical; `bajutsu.adb_resident.narrow_to_active_window` drops the SystemUI decor windows so both
paths yield the same Elements (the equivalence unit 2 of the roadmap item requires).

The Python side that reaches this socket — `adb forward`, the `fetch_hierarchy` wiring, and the
lifecycle tied to the device lease — lives in [`bajutsu/adb_resident.py`](../bajutsu/adb_resident.py)
and is wired into the Android lease in `bajutsu/platform_lifecycle.py` (BE-0245 PR-C). It is opt-in
behind the `BAJUTSU_ADB_RESIDENT` environment variable until the Android e2e lane builds and installs
the server; unset, the adb backend reads via `uiautomator dump` exactly as before.

## Build

```bash
make -C BajutsuAndroidUIAutomatorServer build   # host APK + instrumentation (androidTest) APK
```

This is not part of `make check` (the Python gate builds no Kotlin). It goes through the committed
`./gradlew`, so a fresh clone needs no system-installed Gradle — only the Android SDK.
