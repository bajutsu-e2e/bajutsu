package com.bajutsu.showcase.compose

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import dev.bajutsu.android.BajutsuNet

/**
 * The single launcher Activity. Reads the `SHOWCASE_*` launch-env hooks from intent extras (BE-0007:
 * `launchEnv` → intent extras) and routes the VIEW deeplink to the model. `launchMode="singleTask"` so
 * a deeplink re-selects a tab in the running app via `onNewIntent` rather than starting a second task.
 */
class MainActivity : ComponentActivity() {
    private lateinit var model: AppModel

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val launchEnv = readLaunchEnv(intent)
        // Enable network capture from the injected collector (BE-0283), debug-only like the clipboard
        // receiver (ShowcaseApp.onCreate) — before any Net call, so the interceptor observes from the
        // first request.
        if (BuildConfig.DEBUG) BajutsuNet.configure(launchEnv)
        model = AppModel(launchEnv)
        // A launch via `am start -a VIEW -d <scheme>://<tab>` carries the tab in the intent data.
        intent?.data?.let { model.handleDeepLink(it) }
        setContent {
            MaterialTheme {
                Surface { RootScreen(model) }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.data?.let { model.handleDeepLink(it) }
        // BE-0270: a conformance reseed re-launches this singleTask activity with a new
        // SHOWCASE_CONFORMANCE extra; deliver it to the model so ConformanceScreen re-renders.
        model.applyConformance(intent.getStringExtra("SHOWCASE_CONFORMANCE"))
    }

    // launchEnv (SPEC §3) arrives as string intent extras; read once, defaults live in AppModel.
    private fun readLaunchEnv(intent: Intent?): Map<String, String> {
        val extras = intent?.extras ?: return emptyMap()
        val keys = listOf(
            "SHOWCASE_UITEST", "SHOWCASE_TAB", "SHOWCASE_API_URL", "SHOWCASE_HTTP_BASE",
            "SHOWCASE_GESTURES", "SHOWCASE_CONFORMANCE",
            // The network collector bajutsu injects (BE-0283); BajutsuNet.configure reads these, AppModel ignores them.
            "BAJUTSU_COLLECTOR", "BAJUTSU_COLLECTOR_TOKEN",
        )
        return keys.mapNotNull { key -> extras.getString(key)?.let { key to it } }.toMap()
    }
}
