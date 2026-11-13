package com.bajutsu.showcase.views

import android.annotation.SuppressLint
import android.content.Context
import android.view.View
import android.webkit.WebView
import android.widget.FrameLayout
import android.widget.LinearLayout

/**
 * BE-0482: the Views realization of the hybrid screen, mirroring the iOS HybridWebView. Reached only
 * when the SHOWCASE_WEBVIEW launch env is set (see MainActivity), so the normal five-tab app never
 * renders it. A native title sits over a platform `WebView` that renders [HYBRID_PAGE], so the shared
 * `webview.yaml` scenario resolves the WebView natively by `webview_host` and then taps and types
 * inside its DOM. bajutsu reaches the page over the WebView's DevTools socket, which ShowcaseApp opens
 * with `Bajutsu.enableWebViewDebugging`.
 *
 * The id goes on a container around the WebView, the same as the picker screen's wheels: a
 * `WebView`'s accessibility node comes from its own provider, which reports no resource-id. The
 * container's frame is the WebView's, and the `web:` block matches the page by that frame alone.
 */
class HybridScreen(context: Context) {
    val root: View

    init {
        val title = context.header("Hybrid").aid("webview_title")
        val web = FrameLayout(context).apply {
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_YES
            addView(hybridWebView(context))
        }.aid("webview_host")
        root = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(title)
            addView(web, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        }
    }

    private companion object {
        // The page's button runs an inline script, so JavaScript must be on; the page is a bundled
        // string, never a remote URL, which is what the lint rule guards against.
        @SuppressLint("SetJavaScriptEnabled")
        fun hybridWebView(context: Context): WebView =
            WebView(context).apply {
                settings.javaScriptEnabled = true
                loadDataWithBaseURL(null, HYBRID_PAGE, "text/html", "utf-8", null)
            }

        // Kept identical to the iOS `HybridPage.html` and the Compose twin's copy, so the DOM a `web:`
        // block reads is the same on every platform. `data-testid` is the id a selector inside the
        // block names.
        const val HYBRID_PAGE = """<!doctype html>
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
    }
}
