package com.bajutsu.showcase.compose

import android.app.Application
import dev.bajutsu.android.Bajutsu

/**
 * Registers BajutsuAndroid's clipboard receiver at the process entry point (BE-0233).
 *
 * `Application.onCreate` is the Android peer of BajutsuKit's `App.init()` on iOS: it runs once per
 * process, before any Activity, so the receiver is ready whichever screen bajutsu drives first —
 * unlike an `Activity.onCreate`, which a multi-Activity app might not hit before the broadcast. Debug
 * builds only: the receiver is exported and can read/write the clipboard.
 */
class ShowcaseApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (BuildConfig.DEBUG) Bajutsu.startClipboard(this)
    }
}
