package com.bajutsu.showcase.compose

import android.annotation.SuppressLint
import android.webkit.WebView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import dev.bajutsu.android.Bajutsu

// BE-0482: the Compose realization of the hybrid screen, mirroring the iOS HybridWebView. Reached only
// when the SHOWCASE_WEBVIEW launch env is set (see AppModel / RootScreen). A native title sits over a
// platform `WebView` that renders HYBRID_PAGE, so the shared `webview.yaml` scenario resolves the
// WebView natively by `webview.host` and then taps and types inside its DOM. bajutsu reaches the page
// over the WebView's DevTools socket, which `hybridWebView` opens with `Bajutsu.enableWebViewDebugging`
// right before it creates the WebView.
//
// The id goes on a Box around the `AndroidView`, the same as the picker screen's wheels: the Box's
// frame is the WebView's, and the `web:` block matches the page by that frame alone.
@Composable
fun HybridScreen() {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
            .enableTestTagsAsResourceId(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Hybrid", style = MaterialTheme.typography.headlineMedium, modifier = Modifier.aid("webview.title"))
        Box(Modifier.fillMaxWidth().weight(1f).aid("webview.host")) {
            AndroidView(modifier = Modifier.fillMaxSize(), factory = { context -> hybridWebView(context) })
        }
    }
}

// The page's button runs an inline script, so JavaScript must be on; the page is a bundled string,
// never a remote URL, which is what the lint rule guards against.
//
// Debugging is enabled here rather than in ShowcaseApp because the call loads the WebView provider:
// from `Application.onCreate` it would load it on every launch, and a later overlay update to the
// WebView package then relaunches whatever screen another scenario is driving. The helper is a no-op
// in a build that is not debuggable.
@SuppressLint("SetJavaScriptEnabled")
private fun hybridWebView(context: android.content.Context): WebView {
    Bajutsu.enableWebViewDebugging(context)
    return WebView(context).apply {
        settings.javaScriptEnabled = true
        loadDataWithBaseURL(null, HYBRID_PAGE, "text/html", "utf-8", null)
    }
}

// Kept identical to the iOS `HybridPage.html` and the Views twin's copy, so the DOM a `web:` block
// reads is the same on every platform. `data-testid` is the id a selector inside the block names.
private const val HYBRID_PAGE = """<!doctype html>
<html>
<head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: sans-serif; padding: 16px">
  <h1>Hybrid page</h1>
  <input data-testid="web.name" type="text" aria-label="Name" placeholder="Name">
  <button data-testid="web.greet" onclick="greet()">Greet</button>
  <p data-testid="web.greeting">Waiting</p>
  <script>
    function greet() {
      const name = document.querySelector('[data-testid="web.name"]').value;
      document.querySelector('[data-testid="web.greeting"]').textContent = 'Hello, ' + name;
    }
  </script>
</body>
</html>"""
