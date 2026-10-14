"""Configuration input schema — the pydantic models a `bajutsu.config.yaml` validates into.

The team `defaults` and per-target `targets.<name>` blocks, the groups a target holds, and the
`Config` root (BE-0452). A target's platform-shaped groups are validated by the models its platform
registers in `platform`. Resolution (target -> `Effective`) lives in the sibling `resolve` module,
which reads this schema and produces the frozen output types in `effective`.
"""

from ._functions import _as_list as _as_list
from ._model import _Model as _Model
from .ai_settings import AiSettings
from .config import Config
from .defaults import Defaults, PlatformDefaults
from .device_provider import DeviceProvider
from .doctor_config import DoctorConfig
from .evidence import Evidence
from .hooks import Hooks
from .launch_server import LaunchServer
from .mailbox import Mailbox
from .mock_server import MockServer
from .notify_endpoint import _NOTIFY_EVENTS as _NOTIFY_EVENTS
from .notify_endpoint import NotifyEndpoint
from .paths import Paths
from .platform.web import WEB_ENGINES
from .pricing_entry import PricingEntry
from .run_policy import RunPolicy
from .services import Services
from .target_config import TargetConfig
from .xcuitest_config import XcuitestConfig

__all__ = [
    "WEB_ENGINES",
    "AiSettings",
    "Config",
    "Defaults",
    "DeviceProvider",
    "DoctorConfig",
    "Evidence",
    "Hooks",
    "LaunchServer",
    "Mailbox",
    "MockServer",
    "NotifyEndpoint",
    "Paths",
    "PlatformDefaults",
    "PricingEntry",
    "RunPolicy",
    "Services",
    "TargetConfig",
    "XcuitestConfig",
]
