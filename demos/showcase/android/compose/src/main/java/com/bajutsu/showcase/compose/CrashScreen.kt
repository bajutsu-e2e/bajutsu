package com.bajutsu.showcase.compose

import android.os.Handler
import android.os.Looper
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

// BE-0424: the Compose realization of an app crash, mirroring the iOS CrashView. A unit test can
// stub a `logcat` dump, but only a real fault proves the crash buffer actually carries the
// `FATAL EXCEPTION` block this item's capture extracts, and that `pidof` plus
// `dumpsys activity exit-info` then report the crash the detection depends on. Reached only when the
// SHOWCASE_CRASH launch env is set (see AppModel / RootScreen), so the normal five-tab app (BE-0079)
// never renders it.
//
// Gated behind a launch env rather than a build configuration, mirroring SHOWCASE_CONFORMANCE: a
// Release build would compile a debug-only affordance out, and the expected-to-fail scenario would
// then fail on a missing selector rather than on a crash — the exact misdiagnosis this item exists
// to remove.
@Composable
fun CrashScreen() {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .enableTestTagsAsResourceId(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(24.dp, Alignment.CenterVertically),
    ) {
        Text(text = "Crash fixture", modifier = Modifier.aid("crash.title"))

        // The scenario taps this, then takes one more step against the now-dead app. It has to: a
        // crash caused by a scenario's *last* step is never probed, because the reactive check only
        // asks a driver once a step has already failed, and the tap itself is delivered before the
        // app dies (docs/evidence.md, "App-crash evidence").
        //
        // Posted to the main looper rather than thrown inline: an exception raised inside a Compose
        // click handler unwinds through the framework's own dispatch, which can swallow it into a
        // recomposition error rather than terminating the process. A runnable posted to the main
        // thread throws with nothing above it to catch, so `ActivityManager` reports the uncaught
        // exception and `logcat` gets its `FATAL EXCEPTION` block — the evidence this fixture exists
        // to produce.
        Button(
            onClick = {
                Handler(Looper.getMainLooper()).post {
                    throw IllegalStateException(
                        "SHOWCASE_CRASH: deliberate crash for the BE-0424 app-crash fixture",
                    )
                }
            },
            modifier = Modifier.aid("crash.trigger"),
        ) {
            Text("Crash now")
        }

        // Tapped by the step *after* the trigger. It never actually runs — the app is gone by then —
        // which is the point: that step fails, and its failure is what gets probed.
        Text(text = "Still alive", modifier = Modifier.aid("crash.alive"))
    }
}
