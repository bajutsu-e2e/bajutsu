# BajutsuAndroid

English · [日本語](README.ja.md)

In-app device support for [bajutsu](../) on Android. A test/debug-only Android library that lets
bajutsu drive capabilities the platform only exposes from inside the app process. Today it backs the
**clipboard**; it is the Android peer of the iOS [`BajutsuKit`](../BajutsuKit) package.

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

The receiver is **exported** (it accepts a broadcast from the adb shell uid) and can read and write
the clipboard — **test/debug builds only**. Gate the `startClipboard` call behind `BuildConfig.DEBUG`
(or your own test flag) so it never runs in a release build.

## Coverage

Clipboard set / get / clear on the app's primary clip. `get` reads the first clip item coerced to
text. Non-text clip content (images, intents) is out of scope.
