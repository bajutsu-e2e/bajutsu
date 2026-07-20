# BajutsuAndroid

English · [日本語](README.ja.md)

In-app device support for [bajutsu](../) on Android. A test/debug-only Android library that lets
bajutsu drive capabilities the platform only exposes from inside the app process. It backs the
**clipboard** and **network capture**; it is the Android peer of the iOS [`BajutsuKit`](../BajutsuKit)
package.

## Why in-app support

Since Android 10 (API 29) only the **foreground app and the default IME** may read or write the
primary clip. A shell-uid process therefore cannot: `cmd clipboard set/get-primary-clip` answers
`No shell command implementation` (a silent no-op), and `service call clipboard` hits
`ClipboardService.checkAndSetPrimaryClip` and is brittle across API levels. The app under test *is*
the foreground app while a scenario drives it, so bajutsu sends the clipboard operation to a receiver
**inside** the app, which performs it from the app process.

This keeps bajutsu app-agnostic: every app embeds the *same* library, so it is not a per-app
difference (`targets.<name>` config) — exactly the model BajutsuKit uses for network capture on iOS.

## How it works

bajutsu sends an ordered broadcast, targeted at the app under test:

```
adb shell am broadcast -a dev.bajutsu.CLIPBOARD -p <package> --es op set --es b64 <base64(text)>
```

`am broadcast` acts as the finish-receiver of the ordered broadcast, so the receiver's result comes
back on stdout as `result=<code>, data="<base64>"`. The receiver base64-decodes `set` input and
base64-encodes the clip into the `get` result (a shell-safe alphabet, so the `adb shell` argv needs
no quoting and no scenario text can execute on the device). It sets `result=1` on success; if the app
embeds no receiver the code stays `0`, and bajutsu fails loudly rather than reading an empty clipboard
as success.

## Integrate

Add the library and call `startClipboard(context)` once from `Application.onCreate`, in a
**test/debug build only** — the Android peer of BajutsuKit's `App.init()`. Register it before any
Activity so the receiver is ready whichever screen bajutsu drives first (a launcher `Activity.onCreate`
can be skipped when a multi-Activity app resumes into another entry):

```kotlin
import android.app.Application
import dev.bajutsu.android.Bajutsu

class MyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (BuildConfig.DEBUG) Bajutsu.startClipboard(this)
    }
}
```

Point the manifest's `<application android:name>` at it. The receiver is registered at runtime
(nothing is declared in the manifest), so it exists only when you call `startClipboard`. It is
idempotent, so it never double-registers.

As a Gradle module, include it by path (the showcase does this in
[`demos/showcase/android/settings.gradle.kts`](../demos/showcase/android/settings.gradle.kts)):

```kotlin
include(":bajutsu-android")
project(":bajutsu-android").projectDir = file("../../../BajutsuAndroid")
```

then depend on it: `implementation(project(":bajutsu-android"))`.

## Safety

The receiver is **exported** with no permission, so *any* local app — adb, but also any other app
installed on the device — can send the broadcast and read or write the clipboard. **Test/debug builds
only.** Gate the `startClipboard` call behind `BuildConfig.DEBUG` (or your own test flag) so it never
runs in a release build.

## Network capture (BE-0283)

`request` / `requestSequence` assertions observe traffic through the same in-process collector model
as iOS. When bajutsu runs a scenario with network capture it starts a collector on the host's
`127.0.0.1:<port>`, bridges it to the emulator with `adb reverse`, and injects its URL as the
`BAJUTSU_COLLECTOR` intent extra. Unlike iOS's `URLProtocol`, which swizzles into every `URLSession`
transparently, Android has no single OS-level HTTP hook, so the app adds one line to its OkHttp client
and enables reporting once at launch:

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

The interceptor is inert until `configure` finds `BAJUTSU_COLLECTOR`; when enabled it POSTs each
completed exchange to the collector as JSON matching bajutsu's `NetworkExchange`, over a separate
client so the report is never itself intercepted. OkHttp is a `compileOnly` dependency here — the app
brings its own version.

**Test/debug only.** The interceptor records headers and bodies; gate the `configure` call behind a
debug flag (as shown above) and configure bajutsu's `redact` to mask secrets in the written evidence
(`network.json`) — the same caveat [BajutsuKit's Safety note](../BajutsuKit/README.md#safety) states
for iOS.

**Cleartext exception required.** `BAJUTSU_COLLECTOR` is a plain-HTTP `127.0.0.1` URL, and Android
(API 28+) blocks cleartext traffic by default — unlike iOS, which exempts loopback from ATS. Add a
`network_security_config` cleartext exception for `127.0.0.1` to your test/debug build (see
`demos/showcase/android/*/src/main/res/xml/network_security_config.xml` for the pattern), or the
interceptor's report POST fails with `CLEARTEXT communication to 127.0.0.1 not permitted` — logged but
otherwise silent, so no exchange ever reaches the collector.

## Coverage

Clipboard set / get / clear on the app's primary clip. `get` reads the first clip item coerced to
text. Non-text clip content (images, intents) is out of scope.

Network capture sees **OkHttp-originated HTTP(S)** only — the bounded scope iOS's `URLSession`-only
capture has. Traffic through `HttpURLConnection` directly, a different HTTP client, or a `WebView` is
out of scope (a `WebView` needs its own follow-up, as it did on iOS).
