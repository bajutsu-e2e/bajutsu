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
    /** Broadcast action bajutsu sends clipboard operations on. Must match `bajutsu/adb.py`. */
    const val CLIPBOARD_ACTION: String = "dev.bajutsu.CLIPBOARD"

    /** Result code the receiver sets so a run can tell "handled" from "no receiver". Must match `adb.py`. */
    const val RESULT_OK: Int = 1

    private var registered = false

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
}
