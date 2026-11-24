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
from .hook_command import HookCommand, HookSandbox
from .hooks import Hooks
from .mailbox import Mailbox
from .notify_endpoint import _NOTIFY_EVENTS as _NOTIFY_EVENTS
from .notify_endpoint import NotifyEndpoint
from .paths import Paths
from .pricing_entry import PricingEntry
from .services import Services
from .target_config import TargetConfig
from .xcuitest_config import XcuitestConfig

__all__ = [
    "AiSettings",
    "Config",
    "Defaults",
    "DeviceProvider",
    "DoctorConfig",
    "Evidence",
    "HookCommand",
    "HookSandbox",
    "Hooks",
    "Mailbox",
    "NotifyEndpoint",
    "Paths",
    "PlatformDefaults",
    "PricingEntry",
    "Services",
    "TargetConfig",
    "XcuitestConfig",
]
