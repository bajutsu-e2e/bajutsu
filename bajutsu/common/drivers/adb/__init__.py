"""adb backend (headless, coordinate-based).

Parses `uiautomator dump` XML into normalized Elements and acts via `adb shell input tap/swipe/text`.
adb has no semantic tap, so a tap resolves the target's frame center first — a coordinate
round-trip. Like a device tree that goes near-empty during a screen transition, `uiautomator dump`
intermittently yields a null-root/empty result mid-transition, so this reuses the shared
*resolve-with-retry, fail-ambiguity-fast* discipline unchanged: retry a bounded number of times, and
still fail immediately on an ambiguous (2+) match rather than tapping whatever matched first.

The XML attribute names follow UI Automator's `uiautomator dump` schema; the selector mapping is
`resource-id` (id, package prefix stripped) → `identifier`, `text` → `label`, `content-desc` →
`value`, and the widget `class` (plus `clickable` and enabled/selected/checked state) → `traits`. The value channel
is `content-desc`, not `text`, because the showcase mirrors its assertion state value into
`content-desc` (SPEC §2.1: a `uiautomator dump` exposes `content-desc` but not Compose's
`stateDescription`), while `text` carries the visible label — the Android peer of iOS's
accessibilityLabel / accessibilityValue split. Tuned against the Android showcase on an emulator
(BE-0007 Unit 7): with `text` → `value` a `value` assertion read the visible string ("Matches: 5",
"Not favorited") instead of the mirrored value ("5", "off").

A `clickable` node also carries the `button` trait, and a clickable node with no own `text`/
`content-desc` derives its `label` from its descendants' text — so a Compose `NavigationBarItem`
(a clickable `android.view.View` whose caption lives in a child `TextView`) resolves the shared
cross-backend tab selector `{ label, traits: [button] }` (BE-0107), the same way iOS reaches a tab:
the adb driver catching up to that established contract (BE-0223). Here `button` means *tappable*
(the node responds to a tap), which is broader than a `button` trait derived from the widget type
itself — so a bare `traits: [button]` matches any tappable row or container; pair it with a `label`
(as every shared scenario does) to address one control.
"""

from ._catchup import _Catchup as _Catchup
from ._functions import _BOUNDS as _BOUNDS
from ._functions import _WM_SIZE as _WM_SIZE
from ._functions import _bounds as _bounds
from ._functions import _derived_label as _derived_label
from ._functions import _elements_from_nodes as _elements_from_nodes
from ._functions import _identity as _identity
from ._functions import _native_z_key as _native_z_key
from ._functions import _norm_class as _norm_class
from ._functions import _parse_wm_size as _parse_wm_size
from ._functions import _strip_pkg as _strip_pkg
from ._functions import _to_element as _to_element
from ._functions import _traits as _traits
from ._functions import _warn_malformed_bounds as _warn_malformed_bounds
from ._functions import elements_with_identities, parse_hierarchy, slice_hierarchy_root
from ._shared import NodeIdentity, logger
from .act_outcome import ActOutcome
from .act_request import ActRequest
from .adb_act_uncertain import AdbActUncertain
from .adb_act_unsupported import AdbActUnsupported
from .adb_driver import _UNIT as _UNIT
from .adb_driver import ActFn, AdbDriver, ClockFetch, GestureFn, HierarchyFetch, RunFn, TypeFn
from .adb_gesture_rejected import AdbGestureRejected
from .adb_resident_error import AdbResidentError
from .adb_resident_timeout import AdbResidentTimeout
from .channel_recovery import ChannelRecovery
from .hierarchy_read import HierarchyRead

__all__ = [
    "ActFn",
    "ActOutcome",
    "ActRequest",
    "AdbActUncertain",
    "AdbActUnsupported",
    "AdbDriver",
    "AdbGestureRejected",
    "AdbResidentError",
    "AdbResidentTimeout",
    "ChannelRecovery",
    "ClockFetch",
    "GestureFn",
    "HierarchyFetch",
    "HierarchyRead",
    "NodeIdentity",
    "RunFn",
    "TypeFn",
    "elements_with_identities",
    "logger",
    "parse_hierarchy",
    "slice_hierarchy_root",
]
