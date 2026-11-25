"""Resolved config output types — the frozen shape `resolve` produces for one target.

`Effective` is the deterministic core's view of one target's config: the platform-specific knobs
narrowed behind the `PlatformConfig` union, plus the resolved common-core fields. Its cohesive
field clusters are grouped into their own frozen sub-records (`EvidenceDirs` / `RunDefaults` /
`DoctorThresholds`), the same way `platform_config` narrows the platform axis (BE-0252). The
merge/derivation that builds an `Effective` from the input `schema` lives in the sibling `resolve`
module; nothing here depends on it.
"""

from .ai_config import AiConfig
from .android_config import AndroidConfig
from .doctor_thresholds import DoctorThresholds
from .effective import Effective, PlatformConfig
from .evidence_dirs import EvidenceDirs
from .hook_phases import HookPhases
from .ios_config import IosConfig
from .run_defaults import RunDefaults
from .web_config import WebConfig

__all__ = [
    "AiConfig",
    "AndroidConfig",
    "DoctorThresholds",
    "Effective",
    "EvidenceDirs",
    "HookPhases",
    "IosConfig",
    "PlatformConfig",
    "RunDefaults",
    "WebConfig",
]
