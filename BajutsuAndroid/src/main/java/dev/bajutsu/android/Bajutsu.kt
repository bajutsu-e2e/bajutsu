package dev.bajutsu.android

import android.content.Context
import android.content.IntentFilter
import androidx.core.content.ContextCompat

/**
 * Entry point for BajutsuAndroid, the app-side test support library bajutsu drives on Android.
 *
 * It exists for capabilities the platform only exposes from inside the app process. The first is the
 * clipboard (BE-0233): since Android 10 only the foreground app / default IME may read or write the
 * primary clip, so a shell-uid process cannot — bajutsu instead sends an ordered `am broadcast` that
 * an in-app receiver handles from the app process. This mirrors BajutsuKit on iOS, whose `URLProtocol`
 * captures network traffic the driver can't otherwise see. It stays app-agnostic: every app embeds
 * the *same* library, so it is not a per-app difference (`targets.<name>` config), just like BajutsuKit.
 */
object Bajutsu {
    /** Broadcast action bajutsu sends clipboard operations on. Must match `bajutsu/common/backend_cli/adb.py`. */
    const val CLIPBOARD_ACTION: String = "dev.bajutsu.CLIPBOARD"

    /** Broadcast action bajutsu sends location operations on (BE-0481). Must match `adb/_functions.py`. */
    const val LOCATION_ACTION: String = "dev.bajutsu.LOCATION"

    /** Result code the receiver sets so a run can tell "handled" from "no receiver". Must match `adb.py`. */
    const val RESULT_OK: Int = 1

    /**
     * Result code the location receiver sets when the platform refused the mock provider: the app
     * lacks the mock-location app-op bajutsu grants over adb. Must match `adb/_functions.py`.
     */
    const val RESULT_NO_MOCK_PERMISSION: Int = 2

    /**
     * Result code the location receiver sets when the platform rejected the mock itself; the result
     * data carries the platform's message. Must match `adb/_functions.py`.
     */
    const val RESULT_MOCK_FAILED: Int = 3

    private var registered = false
    private var locationRegistered = false

    /**
     * Register the clipboard receiver so bajutsu can drive the clipboard through this app.
     *
     * Call once from `Application.onCreate`, in a **test/debug build only** — `if (BuildConfig.DEBUG)
     * Bajutsu.startClipboard(this)`. `Application.onCreate` runs before any Activity (the Android peer
     * of BajutsuKit's `App.init()`), so the receiver is ready whichever screen bajutsu drives first.
     * The receiver is exported with no permission, so *any* local app — adb, but also any other app
     * installed on the device — can send the broadcast and set/read the clipboard; it must never ship
     * in a release build. Idempotent: a second call is a no-op.
     */
    fun startClipboard(context: Context) {
        if (registered) return
        ContextCompat.registerReceiver(
            context.applicationContext,
            ClipboardReceiver(),
            IntentFilter(CLIPBOARD_ACTION),
            ContextCompat.RECEIVER_EXPORTED,
        )
        registered = true // only after a successful registration, so a throw doesn't wedge it off
    }

    /**
     * Register the location receiver so bajutsu can mock the device location through this app
     * (BE-0481) — the route `setLocation` takes on a real device, which has no emulator console.
     *
     * Call once from `Application.onCreate`, in a **test/debug build only**, next to
     * [startClipboard]. The receiver is exported with no permission, so any local app can move the
     * app's location once bajutsu has granted the mock-location app-op; it must never ship in a
     * release build. Idempotent: a second call is a no-op.
     */
    fun startLocation(context: Context) {
        if (locationRegistered) return
        ContextCompat.registerReceiver(
            context.applicationContext,
            LocationReceiver(),
            IntentFilter(LOCATION_ACTION),
            ContextCompat.RECEIVER_EXPORTED,
        )
        locationRegistered = true
    }
}
