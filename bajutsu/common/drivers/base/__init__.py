"""Driver abstraction — the linchpin shared by every backend, real or fake.

Frozen first because everything else depends on it:
- common types Point / Element / Selector
- the Driver Protocol (only the actuator performs actions)
- selector resolution (the determinism core): a single action requires a unique
  match, and an ambiguous match (2+) raises AmbiguousSelector to rule out
  nondeterminism structurally.
"""

from ._functions import _collapse_identical_duplicates as _collapse_identical_duplicates
from ._functions import _compile as _compile
from ._functions import _id_index as _id_index
from ._functions import (
    contains,
    deadline_ticks,
    default_wait_for,
    find_all,
    frame_center,
    gesture_anchor,
    id_candidates,
    matches,
    native_z_from_json,
    notification_banner_swipe_points,
    permission_capability,
    raise_if_covered,
    redirect_candidates,
    resolve_unique,
    topmost_at_point,
    validate_id_candidates,
    wait_until,
)
from ._shared import (
    ANDROID_PERMISSION_CAPABILITIES,
    DEVICE_CONTROL_ALL,
    IOS_PERMISSION_CAPABILITIES,
    MAX_REDIRECT_CANDIDATES,
    OS_BACK_BUTTON,
    PERMISSION_SERVICES,
    Frame,
    Point,
)
from .ambiguous_selector import AmbiguousSelector
from .backend_crash_error import BackendCrashError
from .backend_lifecycle import BackendLifecycle
from .background_screenshot_provider import BackgroundScreenshotProvider
from .capability import Capability
from .drained_interruptions import DrainedInterruptions
from .driver import Driver
from .element import Element
from .element_not_found import ElementNotFound
from .element_not_tappable import ElementNotTappable
from .evidence_provider import EvidenceProvider
from .interruption_policy_target import InterruptionPolicyTarget
from .manual_step_required import ManualStepRequired
from .queryable import Queryable
from .raw_source import RawSource
from .raw_source_provider import RawSourceProvider
from .read_lag_provider import ReadLagProvider
from .read_order_provider import ReadOrderProvider
from .selector import Selector
from .selector_error import SelectorError
from .settled_cache_invalidator import SettledCacheInvalidator
from .settled_read_provider import SettledReadProvider
from .trait import Trait
from .unsupported_action import UnsupportedAction
from .viewport_provider import ViewportProvider

__all__ = [
    "ANDROID_PERMISSION_CAPABILITIES",
    "DEVICE_CONTROL_ALL",
    "IOS_PERMISSION_CAPABILITIES",
    "MAX_REDIRECT_CANDIDATES",
    "OS_BACK_BUTTON",
    "PERMISSION_SERVICES",
    "AmbiguousSelector",
    "BackendCrashError",
    "BackendLifecycle",
    "BackgroundScreenshotProvider",
    "Capability",
    "DrainedInterruptions",
    "Driver",
    "Element",
    "ElementNotFound",
    "ElementNotTappable",
    "EvidenceProvider",
    "Frame",
    "InterruptionPolicyTarget",
    "ManualStepRequired",
    "Point",
    "Queryable",
    "RawSource",
    "RawSourceProvider",
    "ReadLagProvider",
    "ReadOrderProvider",
    "Selector",
    "SelectorError",
    "SettledCacheInvalidator",
    "SettledReadProvider",
    "Trait",
    "UnsupportedAction",
    "ViewportProvider",
    "contains",
    "deadline_ticks",
    "default_wait_for",
    "find_all",
    "frame_center",
    "gesture_anchor",
    "id_candidates",
    "matches",
    "native_z_from_json",
    "notification_banner_swipe_points",
    "permission_capability",
    "raise_if_covered",
    "redirect_candidates",
    "resolve_unique",
    "topmost_at_point",
    "validate_id_candidates",
    "wait_until",
]
