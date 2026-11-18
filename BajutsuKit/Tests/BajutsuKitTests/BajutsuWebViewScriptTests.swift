import XCTest
@testable import BajutsuKit

/// The bridge's script templates (BE-0037), which a `web:` block runs inside the page. Building one
/// used to abort the app: a bare string handed to `JSONSerialization` raises an Objective-C
/// exception that `try?` cannot catch (BE-0482 found it on the showcase's first WebView screen).
final class BajutsuWebViewScriptTests: XCTestCase {
    func testTypeEmbedsTheTextAsAJSONStringLiteral() {
        let js = BajutsuWebView.typeJS(text: "a \"quoted\" name")
        XCTAssertTrue(js.contains(#"el.value += "a \"quoted\" name";"#), js)
    }

    func testScrollQuotesTheTestIDSoADottedIDIsAValidSelector() {
        let js = BajutsuWebView.scrollJS(elementId: "web.name")
        // `[data-testid=web.name]` is no valid CSS selector; the value must reach it quoted.
        XCTAssertTrue(js.contains(#"'[data-testid="' + CSS.escape("web.name") + '"]'"#), js)
    }

    func testTapFocusesTheElementBeforeClickingIt() throws {
        let js = BajutsuWebView.tapJS(x: 1, y: 2)
        let focus = try XCTUnwrap(js.range(of: "el.focus()"))
        let click = try XCTUnwrap(js.range(of: "el.click()"))
        XCTAssertLessThan(focus.lowerBound, click.lowerBound)
    }
}
