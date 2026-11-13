import SwiftUI
import WebKit

// BE-0482: the hybrid screen, reached only under the SHOWCASE_WEBVIEW launch env. A native title sits
// over a `WKWebView` that renders `HybridPage.html`, so the shared `webview.yaml` scenario resolves
// the WebView natively by `webview.host` and then taps and types inside its DOM. The page is inline
// rather than fetched, so the run depends on no network. The Android twins render the same page, byte
// for byte, which is what lets one scenario pass on every platform.
struct HybridWebView: View {
    var body: some View {
        VStack(spacing: 12) {
            Text("Hybrid")
                .font(.title)
                .accessibilityID("webview.title")
            HybridWebViewHost()
        }
        .padding()
    }
}

private struct HybridWebViewHost: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let view = WKWebView()
        // The BajutsuKit WebView bridge finds the view by this identifier, and the scenario's
        // `within` names it, so it is the one link between the native tree and the page.
        view.accessibilityID("webview.host")
        view.loadHTMLString(HybridPage.html, baseURL: nil)
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}
}

enum HybridPage {
    // Kept identical to the Android twins' `HYBRID_PAGE`, so the DOM a `web:` block reads is the same
    // on every platform. `data-testid` is the id a selector inside the block names.
    static let html = """
    <!doctype html>
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
    </html>
    """
}
