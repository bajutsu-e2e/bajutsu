"""The normalized accessibility traits every backend maps its own onto."""

from __future__ import annotations


class Trait:
    """Normalized accessibility traits.

    Drivers normalize at least the following to these common tokens. `NOT_ENABLED` and
    `SELECTED` back state assertions and `BUTTON` / `LINK` the `traits` selector and doctor
    check; `OTHER` instead backs `resolve_unique`'s ambiguity filtering (below).
    """

    BUTTON = "button"
    LINK = "link"
    NOT_ENABLED = "notEnabled"  # disabled state (enabled / disabled assertions)
    SELECTED = "selected"  # selected / toggled state (selected assertion)
    OTHER = "other"  # generic/unclassified element (e.g. iOS's catch-all XCUIElementTypeOther)
    # A field the platform itself marks secret, so redaction masks its value with no configuration
    # (BE-0331). The token is XCUITest's own type name because iOS already reported it; web and
    # Android map their own source onto it (`input[type=password]`, the node's `password` flag), so
    # one construct means the same thing on every backend. Pinned by the conformance suite.
    SECURE_TEXT_FIELD = "secureTextField"
    # A wheel picker one `setPickerValue` step can set (BE-0480). The token is XCUITest's own type
    # name, as with `SECURE_TEXT_FIELD`: iOS already reported it, and Android maps
    # `android.widget.NumberPicker` onto it, so one selector addresses a wheel on both platforms.
    # Pinned by the conformance suite.
    PICKER_WHEEL = "pickerWheel"
