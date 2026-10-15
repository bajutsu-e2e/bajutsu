"""The cap on how many actuation records one drain may carry."""

from __future__ import annotations

# The driver primitives a record may name. `str` rather than a Literal on purpose: the report
# reconstructs these records from a manifest an older or newer version of the tool wrote (BE-0068
# makes that compatibility the loader's contract), so a Literal would assert at the type level a
# guarantee the file on disk cannot make. A reader treats an unlisted value as opaque text.
GESTURES: tuple[str, ...] = (
    "tap",
    "doubleTap",
    "longPress",
    "swipe",
    "scroll",
    "pinch",
    "rotate",
    "typeText",
    "deleteText",
    "selectAll",
    "copy",
    "selectOption",
    "setPickerValue",
    "systemAlert",
    "back",
    "navigate",
)

# How a gesture reached its target. `coordinate` is the only value that implies `points`.
CHANNELS: tuple[str, ...] = (
    "coordinate",  # the driver computed (or was handed) a point and sent it
    "handle",  # XCUITest actuated a snapshot handle; it chose the point
    "identity",  # the Android device resolved the element and chose the point
    "bridge",  # a WebView bridge call addressed by element id, which picks its own coordinate
    "focused",  # a text primitive on whatever field holds focus, addressing no element
    "key",  # a key event (Android's system back), no coordinate at all
    "history",  # browser history navigation
    "url",  # a page load by URL (`navigate`); the URL itself stays out of the record
)

# Why the element actuated is not the one the driver's default rule would have named. Absent on the
# ordinary path. This is a separate axis from `via`, which answers how the gesture reached its target:
# a substituted tap still travels by `handle`; what changed is *which* element. Like `GESTURES`, an
# unlisted value is opaque text to a reader rather than an error, so an older report stays loadable.
SUBSTITUTIONS: tuple[str, ...] = (
    # The tap resolved uniquely but was refused, and exactly one named descendant inside its frame
    # was reachable — a container inflated over the control it wraps (BE-XXXX).
    "soleHittableDescendant",
)

# The coordinate space a record's numbers live in. iOS reports points, Android raw pixels, a browser
# (and a WebView's own space) CSS pixels — so a coordinate is only comparable alongside its space.
UNITS: tuple[str, ...] = ("point", "pixel", "cssPixel")
