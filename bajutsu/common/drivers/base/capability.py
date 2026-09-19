"""The capability names a backend declares, so a step can fail before it runs."""

from __future__ import annotations


class Capability:
    """Capability names returned by Driver.capabilities().

    Used to pick the actuator and resolve fallbacks. A backend with SEMANTIC_TAP
    actuates more stably (no coordinates involved).
    """

    QUERY = "query"
    SEMANTIC_TAP = "semanticTap"  # tap directly by id/label (no coordinates; most stable)
    CONDITION_WAIT = "conditionWait"  # native condition waiting
    NETWORK = "network"  # native network monitoring
    SCREENSHOT = "screenshot"
    ELEMENTS = "elements"
    MULTI_TOUCH = (
        "multiTouch"  # two-finger gestures (pinch / rotate); a single-touch backend lacks it
    )
    WEBVIEW = "webView"  # DOM query/tap inside an embedded WKWebView (BE-0037)
    SELECT_OPTION = "selectOption"  # set a native <select> by value; web only (BE-0191)
    # `select`/`copy` on the focused field (BE-0265). A backend that can select and copy natively
    # advertises this; a coordinate-only backend with no select-all handle does not and raises
    # UnsupportedAction — the same actuate-or-raise promise as MULTI_TOUCH (BE-0280). `delete` needs
    # no token: every backend actuates `delete_text` (a run of backspaces). `clear` needs no token
    # either — it opportunistically selects-all-then-backspaces-once where this is advertised, and
    # falls back to a counted `delete_text` run everywhere else.
    TEXT_SELECTION = "textSelection"
    # Tap a button on an out-of-process iOS SpringBoard permission prompt by a native accessibility
    # query, deterministically (BE-0316). Only the resident-runner XCUITest backend advertises it:
    # SpringBoard alert access is an on-device XCUITest capability, not a simctl operation, so it is a
    # top-level token like MULTI_TOUCH rather than a `deviceControl.*` one. Android reaches a system
    # dialog through an ordinary `tap`, and the web backend has no OS-level prompt, so neither needs it.
    HANDLE_SYSTEM_ALERT = "handleSystemAlert"
    # Set a wheel-style picker (`UIPickerView`, a wheel-mode `UIDatePicker`) to an exact value
    # (BE-0356). Only the resident-runner XCUITest backend advertises it: a picker wheel is an
    # iOS control, and XCUITest's `adjust(toPickerWheelValue:)` is what makes landing on a named
    # row deterministic — the mirror image of SELECT_OPTION, which only the web backend can honor.
    PICKER_WHEEL = "pickerWheel"
    # Activate an app the test target never started, by bundle id, and drive it until a matching
    # leave (the `app:` step). Only the resident-runner XCUITest backend advertises it: a
    # feasibility spike confirmed `XCUIApplication(bundleIdentifier:).activate()` reliably
    # foregrounds an uncooperative app with no per-run configuration (unlike WEBVIEW, whose DOM
    # bridge availability is a per-run fact, not a fixed backend property) — a real Simulator
    # capability, not a live-grid one, so `xcuitest_live` does not advertise it either (first slice).
    APP_CONTEXT = "appContext"
    # Dismiss a blocking Apple TipKit tip — an in-app popover the framework, not the app, builds —
    # by its TipKit-internal dismiss region. Only the XCUITest backend advertises it: TipKit is an
    # iOS framework, and the identifier it exposes is the driver's knowledge to hold, not the
    # orchestrator's. Unlike HANDLE_SYSTEM_ALERT the tip is in-process, so this needs no runner route
    # — but it stays a token so an iOS-only identifier never reaches the backend-agnostic core.
    HANDLE_TIPKIT_TIP = "handleTipkitTip"
    # Report a foreground notification banner's own frame, when one is showing (BE-0416). Only the
    # resident-runner XCUITest backend advertises it: the banner is a SpringBoard element, on-device
    # XCUITest access the same way HANDLE_SYSTEM_ALERT is, and no other backend has an equivalent
    # foreground-banner surface to report. The interruption-monitor path that answers a banner
    # blocking an in-flight interaction (BE-0416 Unit 4) needs no token of its own — it already holds
    # the element XCUITest handed it — so this token gates only the proactive presence query and the
    # swipe it feeds (Units 2/3/8), used opportunistically between interactions.
    HANDLE_NOTIFICATION_BANNER = "handleNotificationBanner"
    # The `DeviceControl` family, one token per operation (BE-0212, split from the coarse
    # `deviceControl` of BE-0128). A backend advertises exactly the operations it can honor, so
    # preflight gates each device-control step on its own operation — the Android emulator backs
    # setLocation + clipboard but not the rest. Operations that always ship together share a token
    # (the clipboard read/write/clear trio; background/foreground; the status-bar override/clear pair).
    DC_SET_LOCATION = "deviceControl.setLocation"
    DC_CLIPBOARD = "deviceControl.clipboard"  # setClipboard / getClipboard / clearClipboard
    DC_PUSH = "deviceControl.push"
    DC_CLEAR_KEYCHAIN = "deviceControl.clearKeychain"
    DC_APP_LIFECYCLE = "deviceControl.appLifecycle"  # background / foreground
    DC_STATUS_BAR = "deviceControl.statusBar"  # overrideStatusBar / clearStatusBar
    # `permissions` (BE-0276) is gated per-service, not by one token: iOS and Android honor
    # different subsets of the shared vocabulary (iOS has no TCC service for `notifications`), so a
    # single `deviceControl.permissions` token could not tell preflight which services are actually
    # supported. See `permission_capability` / `PERMISSION_SERVICES` below.
