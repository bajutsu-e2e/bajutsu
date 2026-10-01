"""The point and frame geometry the driver seam and its selector resolution are expressed in."""

from __future__ import annotations

from ._functions import permission_capability
from .capability import Capability

# The iOS navigation bar's OS-provided back button (accessibility identifier "BackButton"). iOS has
# no hardware/system back, so the iOS backend (XCUITest) navigates back by tapping it — a
# platform convention, not app-specific — so the id lives in one shared place (BE-0210).
OS_BACK_BUTTON = "BackButton"

# Coordinates in points: x, y.
Point = tuple[float, float]
# frame: x, y, w, h in points.
Frame = tuple[float, float, float, float]


# The permission vocabulary a scenario's `permissions` field may name (BE-0276); imported directly
# by `bajutsu.common.scenario.models.scenario.Scenario`'s `permissions` field validator rather than
# duplicated there, since the scenario models already depend on this module.
PERMISSION_SERVICES: tuple[str, ...] = (
    "location",
    "camera",
    "microphone",
    "contacts",
    "photos",
    "calendar",
    "notifications",
)


# The whole `DeviceControl` family as a set of per-operation tokens (BE-0212). A backend that backs
# the entire family (xcuitest, via the iOS Simulator lifecycle) advertises this in one shot;
# one that backs a subset (Android) lists only its operations' tokens.
DEVICE_CONTROL_ALL = frozenset(
    {
        Capability.DC_SET_LOCATION,
        Capability.DC_CLIPBOARD,
        Capability.DC_PUSH,
        Capability.DC_CLEAR_KEYCHAIN,
        Capability.DC_BACKGROUND,
        Capability.DC_FOREGROUND,
        Capability.DC_STATUS_BAR,
    }
)

# The permission services iOS's `simctl privacy` backs — every vocabulary entry but `notifications`
# (iOS notification authorization is not part of TCC — Transparency, Consent, and Control — the
# database `simctl privacy` drives). Provided by xcuitest, which wires a real
# simctl-backed `DeviceControl` via the iOS Simulator lifecycle (mirrors `DEVICE_CONTROL_ALL`).
IOS_PERMISSION_CAPABILITIES = frozenset(
    permission_capability(s) for s in PERMISSION_SERVICES if s != "notifications"
)

# The permission services Android's `pm grant`/`pm revoke` backs — the full vocabulary, including
# `notifications` (`POST_NOTIFICATIONS` is a runtime permission since API 33).
ANDROID_PERMISSION_CAPABILITIES = frozenset(permission_capability(s) for s in PERMISSION_SERVICES)


# Above this many named descendants, a refused actuation fails rather than probing them: a container
# this crowded is a layout region, not a control with one actuatable child, and every probe a backend
# spends asking "is this one reachable" is a round trip.
MAX_REDIRECT_CANDIDATES = 4
