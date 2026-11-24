"""The transport seam, timeouts, and reply shapes the runner channel is spoken over."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ._reply import _Reply

# (method, path, json body) -> decoded reply. Injectable so the channel logic is tested without a
# runner; the default talks HTTP to the runner's loopback server.
TransportFn = Callable[[str, str, Mapping[str, Any] | None], _Reply]

# bundle id (None for the app under test) -> the lowercased URL schemes its build declares, or None
# when the environment cannot read that build (BE-0487).
UrlSchemeSource = Callable[[str | None], frozenset[str] | None]

# Statuses the runner returns for an actuation request. `ok` succeeds; `stale` / `not-found` are test
# outcomes (the element vanished / could not be actuated); any other status is a runner/infra error.
_OK = "ok"

# The two nodes a showing TipKit tip is recognized by: the full-screen "tap outside to close" scrim a
# popover installs, and the tip's own container. The scrim is not TipKit's alone — measured
# on-device, a SwiftUI `confirmationDialog` installs one carrying the same identifier, the same
# "dismiss popup" label, and the same full-screen frame — so keying on the scrim by itself cannot
# tell a tip from an app's own popover, and dismissing by tapping it would close that app's dialog.
# Requiring the container as well identifies what the guard is for, leaving an unrecognized popover
# alone by default. The container is a detection signal only, measured: tapping it leaves the tip up
# (the tree is unchanged, container and scrim both still there), while tapping the close button clears
# it. So the scrim stays the dismiss target, as BE-0389 established. The tip's third node, the close button, is
# not part of the pair — its identifier is the SF Symbol name `xmark.circle.fill`, which an unrelated
# app-authored button could plausibly reuse and turn into an `AmbiguousSelector`.
_TIPKIT_DISMISS_REGION = "PopoverDismissRegion"
