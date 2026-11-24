package com.bajutsu.showcase.views

import android.app.Application
import dev.bajutsu.android.Bajutsu

/**
 * Registers BajutsuAndroid's clipboard (BE-0233), location (BE-0481), and push (BE-0498) receivers at the process
 * entry point.
 *
 * `Application.onCreate` is the Android peer of BajutsuKit's `App.init()` on iOS: it runs once per
 * process, before any Activity, so the receiver is ready whichever screen bajutsu drives first. This
 * app is multi-Activity (a detail screen can be the resumed entry after process death), so the
 * process-wide entry point is the correct place, not a single Activity's `onCreate`. Debug builds
 * only: the receiver is exported and can read/write the clipboard.
 */
class ShowcaseApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (BuildConfig.DEBUG) {
            Bajutsu.startClipboard(this)
            Bajutsu.startLocation(this) // the in-app setLocation route on a real device (BE-0481)
            // The `push` step on Android (BE-0498): the handler path, so the suite exercises the
            // app's own handling; the readout on the Permissions tab shows the pushed title.
            Bajutsu.startPush(this) { push -> PushInbox.receive(push.title.orEmpty()) }
        }
    }
}
