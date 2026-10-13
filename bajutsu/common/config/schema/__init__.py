"""Configuration input schema — the pydantic models a `bajutsu.config.yaml` validates into.

The team `defaults` and per-target `targets.<name>` blocks, their field validators, and the
`Config` root. Resolution (defaults overlaid by target -> `Effective`) lives in the sibling `resolve`
module, which reads this schema and produces the frozen output types in `effective`; nothing
here depends on `resolve` except the one deferred back-reference in `Config`'s validator.
"""

from ._functions import _as_list as _as_list
from ._functions import _check_platform as _check_platform
from ._model import _Model as _Model
from .ai_settings import AiSettings
from .config import Config
from .defaults import Defaults
from .device_provider import DeviceProvider
from .doctor_config import DoctorConfig
from .launch_server import LaunchServer
from .mailbox import Mailbox
from .mock_server import MockServer
from .notify_endpoint import _NOTIFY_EVENTS as _NOTIFY_EVENTS
from .notify_endpoint import NotifyEndpoint
from .platform.web import WEB_ENGINES
from .pricing_entry import PricingEntry
from .target_config import TargetConfig
from .xcuitest_config import XcuitestConfig

__all__ = [
    "WEB_ENGINES",
    "AiSettings",
    "Config",
    "Defaults",
    "DeviceProvider",
    "DoctorConfig",
    "LaunchServer",
    "Mailbox",
    "MockServer",
    "NotifyEndpoint",
    "PricingEntry",
    "TargetConfig",
    "XcuitestConfig",
]
