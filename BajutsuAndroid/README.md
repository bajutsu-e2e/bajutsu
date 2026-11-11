# BajutsuAndroid

English · [日本語](README.ja.md)

BajutsuAndroid is the in-app device support library for [bajutsu](../) on Android. It is a test/debug-only
Android library that lets bajutsu drive capabilities the platform exposes only from inside the app
process. It backs the **clipboard**, the **location** on a real device, and **network capture**. It is
the Android peer of the iOS
[`BajutsuKit`](../BajutsuKit) package.

## Why in-app support

Since Android 10 (API 29), only the **foreground app and the default input method editor (IME)** may
read or write the primary clip. A process running as the shell user therefore cannot do so:

- `cmd clipboard set/get-primary-clip` answers `No shell command implementation`, a silent no-op.
- `service call clipboard` hits `ClipboardService.checkAndSetPrimaryClip` and is brittle across API levels.

While a scenario drives the app under test, that app is the foreground app. So bajutsu sends the
clipboard operation to a receiver **inside** the app, and the receiver performs it from the app process.

This design keeps bajutsu app-agnostic. Every app embeds the *same* library, so the library adds no
per-app difference (`targets.<name>` config). BajutsuKit uses the same model for network capture on iOS.

## How it works

bajutsu sends an ordered broadcast, targeted at the app under test:

```
adb shell am broadcast -a dev.bajutsu.CLIPBOARD -p <package> --es op set --es b64 <base64(text)>
```

`am broadcast` acts as the finish-receiver of the ordered broadcast. The receiver's result therefore
comes back on stdout as `result=<code>, data="<base64>"`.

The receiver base64-decodes the input of `set` and base64-encodes the clip into the result of `get`.
Base64 is a shell-safe alphabet, so the `adb shell` argv needs no quoting and no scenario text can
execute on the device.

The receiver sets `result=1` on success. If the app embeds no receiver, the code stays `0`, and bajutsu
fails loudly rather than reading an empty clipboard as success.

## Integrate

Add the library, then call `startClipboard(context)` and `startLocation(context)` once from
`Application.onCreate`, in a **test/debug build only**. These calls are the Android peer of
BajutsuKit's `App.init()`.

Register the receiver before any Activity, so it is ready whichever screen bajutsu drives first. A
launcher `Activity.onCreate` can be skipped when a multi-Activity app resumes into another entry.

```kotlin
import android.app.Application
import dev.bajutsu.android.Bajutsu

class MyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (BuildConfig.DEBUG) {
            Bajutsu.startClipboard(this)
            Bajutsu.startLocation(this)
        }
    }
}
```

Point the manifest's `<application android:name>` at this class. The manifest declares neither
receiver. Each `start` function registers its receiver at runtime. A receiver exists once you call its
function, and not before. Each call is idempotent, so it never double-registers. An app that does not need the
location on a real device can skip `startLocation`.

To use the library as a Gradle module, include it by path. The showcase does this in
[`demos/showcase/android/settings.gradle.kts`](../demos/showcase/android/settings.gradle.kts):

```kotlin
include(":bajutsu-android")
project(":bajutsu-android").projectDir = file("../../../BajutsuAndroid")
```

Then depend on the module: `implementation(project(":bajutsu-android"))`.

## Safety

Both receivers are **exported** with no permission. *Any* local app can therefore send the broadcast,
whether through adb or as another app installed on the device. Such an app can read or write the
clipboard, and it can move the app's location while bajutsu holds the mock-location grant.

Use the library in **test/debug builds only**. Gate the `startClipboard` and `startLocation` calls
behind `BuildConfig.DEBUG` (or your own test flag), so the calls never run in a release build.

## Location on a real device

This section covers the location route that [BE-0481](../roadmaps/BE-0481-android-device-set-location/BE-0481-android-device-set-location.md)
added. A `setLocation` step needs no library on a local emulator. bajutsu sets the fix there through the
emulator console (`adb emu geo fix`). A real device has no console, and neither has an emulator that
adb reaches with `adb connect`. On those targets bajutsu moves the location through the app instead:

1. It grants the app the mock-location app-op:
   `adb shell appops set <package> android:mock_location allow`.
2. It sends an ordered broadcast with the coordinates as strings:
   `adb shell am broadcast -a dev.bajutsu.LOCATION -p <package> --es op set --es lat <lat> --es lon <lon>`.
3. The receiver registers the `gps` and `network` providers as test providers with
   `LocationManager.addTestProvider`, enables them, and sets the location on each.

bajutsu picks the route on the first `setLocation` of a lease. The console route needs two things:

- an `emulator-<port>` serial
- the `ro.boot.qemu` or `ro.kernel.qemu` property set to `1`

Every other target takes the in-app route.

The receiver answers with one of four result codes:

| Result | Meaning | What bajutsu reports |
|---|---|---|
| `1` | The location is set or cleared | — |
| `2` | `addTestProvider` raised `SecurityException` | The app lacks the mock-location app-op |
| `3` | A provider call raised `IllegalArgumentException` | The platform's message, from the result data |
| `0` | No receiver handled the broadcast | The app does not call `startLocation` |

The test providers may not survive the app process that registered them. They survived a
force-stop and relaunch on an API 34 emulator, but other images and devices may differ. bajutsu sends
the last location again after each launch it makes:

- a `relaunch` step
- the launch after an `installApp` step
- a crawl's reset
 When the lease ends, bajutsu runs three steps:

1. It broadcasts `clear`, which removes both test providers. When the app is no longer running,
   bajutsu launches it first, because the providers outlive the process and only the app can remove them.
2. It resets the app-op with `appops set <package> android:mock_location default`.
3. It force-stops the app, as every lease's end does.

The next scenario on the device starts from the device's real location. If the first or second step goes wrong,
bajutsu logs a warning. The scenario's verdict stays as it was.

The library mocks the `gps` and `network` providers alone. It leaves the fused provider of Google
Play services unmocked. The library thus takes no Play services dependency. An app may read the
location through `FusedLocationProviderClient`. Whether that app sees the mock depends on the device
and its Play services version. Nobody has checked that behavior on hardware yet.

The library declares no `ACCESS_MOCK_LOCATION` permission. On the emulator images bajutsu tests,
`appops set` grants the app-op without it.

## Network capture (BE-0283)

`request` / `requestSequence` assertions observe traffic through the same in-process collector model
as iOS. When bajutsu runs a scenario with network capture, it does three things:

1. It starts a collector on the host's `127.0.0.1:<port>`.
2. It bridges the collector to the emulator with `adb reverse`.
3. It injects the collector's URL as the `BAJUTSU_COLLECTOR` intent extra.

iOS's `URLProtocol` swizzles into every `URLSession` transparently. Android has no single OS-level HTTP
hook, so the app adds one line to its OkHttp client and enables reporting once at launch:

```kotlin
import dev.bajutsu.android.BajutsuNet

// Once at launch, from the same launch-env map you already read (a no-op unless bajutsu injected a
// collector, so it is safe to call unconditionally in a test/debug build):
BajutsuNet.configure(launchEnv)

// On the OkHttpClient the app under test builds:
val client = OkHttpClient.Builder()
    .addInterceptor(BajutsuNet.interceptor())
    .build()
```

The interceptor is inert until `configure` finds `BAJUTSU_COLLECTOR`. Once enabled, it POSTs each
completed exchange to the collector as JSON matching bajutsu's `NetworkExchange`. The POST goes over a
separate client, so the report is never itself intercepted. OkHttp is a `compileOnly` dependency here,
and the app brings its own version.

**Test/debug only.** The interceptor records headers and bodies. Gate the `configure` call behind a
debug flag, as shown above. Also configure bajutsu's `redact` to mask secrets in the written evidence
(`network.json`). [BajutsuKit's Safety note](../BajutsuKit/README.md#safety) states the same caveat for
iOS.

**Cleartext exception required.** `BAJUTSU_COLLECTOR` is a plain-HTTP `127.0.0.1` URL. Android
(API 28+) blocks cleartext traffic by default, unlike iOS, which exempts loopback from App Transport
Security (ATS).

Add a `network_security_config` cleartext exception for `127.0.0.1` to your test/debug build. The
pattern is in `demos/showcase/android/*/src/main/res/xml/network_security_config.xml`. Without the
exception, the interceptor's report POST fails with
`CLEARTEXT communication to 127.0.0.1 not permitted`. The failure is logged but otherwise silent, so no
exchange ever reaches the collector.

## Coverage

The clipboard support covers set, get, and clear on the app's primary clip. `get` reads the first clip
item coerced to text. Non-text clip content (images, intents) is out of scope.

The location support covers the `gps` and `network` providers. The fused provider is out of scope.

Network capture sees **OkHttp-originated HTTP(S)** traffic alone. This is the same bounded scope as
iOS's `URLSession`-only capture. The following are out of scope:

- Traffic through `HttpURLConnection` directly
- Traffic through a different HTTP client
- Traffic through a `WebView`, which needs its own follow-up, as it did on iOS
