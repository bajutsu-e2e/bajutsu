"""The live-route XCUITest transport — W3C WebDriver against a reserved iOS device (BE-0238).

The local XCUITest path (`drivers/xcuitest.py`) drives a resident BajutsuKit runner over a bespoke
loopback HTTP channel. A device cloud exposes no such runner — only a W3C WebDriver endpoint
(Appium's XCUITest driver) for a device it has reserved. So the *live* route speaks W3C WebDriver to
that endpoint directly from Python, rather than tunnelling the runner channel to a port the grid does
not serve.

Two pieces live here, both faked at the network boundary so no grid is needed on the gate:

- `WebDriverClient` — a minimal in-house W3C client built on `http.client` and injected the same way
  `XcuitestDriver` injects its transport, so the wire mapping is exercised against a fake. It keeps
  the gate free of a third-party WebDriver dependency and matches the runner channel's own stdlib
  client.
- `XcuitestLiveDriver` — the driver, which reuses the shape of `XcuitestDriver`: query the whole
  screen with one broad locator, build the `base.Element` list, resolve the selector Python-side with
  `resolve_unique` (so an ambiguous selector fails immediately — determinism first, prime directive 2)
  and act on the chosen element by the WebDriver element id the query returned. The element id stands
  in for the runner's opaque handle in the same query-resolve-act-by-handle flow.

Slice A landed session lifecycle, `query` / `tap` / `screenshot` / readiness. Slice B wires input and
gestures onto Appium's XCUITest `mobile:` commands (over `POST /execute/sync`), the driver's native
counterparts of the local runner's semantic endpoints: a coordinate tap, double tap, touch-and-hold,
drag, pinch, and rotate, plus text entry through W3C send-keys to the active element. `selectAll` and
`copy` have no first-class Appium XCUITest command — the local runner does them natively — and
`paste`'s edit-menu composition is not wired onto this route, so all three fail loudly on the live
route rather than silently no-op'ing (determinism first). The run-time
capability narrowing, config, and docs (Slice C) are the remaining follow-on.
"""

from ._functions import _READ_TIMEOUT_SECONDS as _READ_TIMEOUT_SECONDS
from ._functions import _WRITE_TIMEOUT_SECONDS as _WRITE_TIMEOUT_SECONDS
from ._functions import _is_true as _is_true
from ._functions import _norm_type as _norm_type
from ._functions import _raw_wd_transport as _raw_wd_transport
from ._functions import _str_or_none as _str_or_none
from ._functions import _timeout_for as _timeout_for
from ._shared import WdTransportFn
from .web_driver_client import ELEMENT_KEY, WebDriverClient
from .web_driver_error import WebDriverError
from .xcuitest_live_driver import _DRAG_DURATION_SECONDS as _DRAG_DURATION_SECONDS
from .xcuitest_live_driver import _PINCH_VELOCITY as _PINCH_VELOCITY
from .xcuitest_live_driver import _ROTATE_VELOCITY as _ROTATE_VELOCITY
from .xcuitest_live_driver import _SCROLL_DURATION_SECONDS as _SCROLL_DURATION_SECONDS
from .xcuitest_live_driver import _UNIT as _UNIT
from .xcuitest_live_driver import BACKSPACE_KEY, XcuitestLiveDriver

__all__ = [
    "BACKSPACE_KEY",
    "ELEMENT_KEY",
    "WdTransportFn",
    "WebDriverClient",
    "WebDriverError",
    "XcuitestLiveDriver",
]
