"""Configuration — team defaults overlaid by per-target config.

Split along its four responsibilities (BE-0252), one submodule each, with a one-directional
dependency from resolution outward: input schema (`schema`) -> resolved output types
(`effective`) <- merge/derivation (`resolve`) and platform-narrowing accessors (`accessors`).
The public API is re-exported here, so `from bajutsu.config import Effective, resolve,
require_ios, …` is unchanged and no call site outside this package is touched.
"""

from __future__ import annotations

from bajutsu.config.accessors import (
    android_package,
    ios_bundle_id,
    require_android,
    require_ios,
    require_web,
    web_base_url,
    web_engine,
    xcuitest_targets_real_device,
)
from bajutsu.config.effective import (
    AiConfig,
    AndroidConfig,
    DoctorThresholds,
    Effective,
    EvidenceDirs,
    IosConfig,
    PlatformConfig,
    RunDefaults,
    WebConfig,
)
from bajutsu.config.resolve import load_config, parse_config_dict, resolve
from bajutsu.config.schema import (
    WEB_ENGINES,
    AiSettings,
    Config,
    Defaults,
    DeviceProvider,
    DoctorConfig,
    LaunchServer,
    Mailbox,
    MockServer,
    NotifyEndpoint,
    PricingEntry,
    TargetConfig,
    XcuitestConfig,
)
from bajutsu.config.schema import (
    _Model as _Model,  # redundant alias: re-exported for callers importing the shared pydantic base
)

__all__ = [
    "WEB_ENGINES",
    "AiConfig",
    "AiSettings",
    "AndroidConfig",
    "Config",
    "Defaults",
    "DeviceProvider",
    "DoctorConfig",
    "DoctorThresholds",
    "Effective",
    "EvidenceDirs",
    "IosConfig",
    "LaunchServer",
    "Mailbox",
    "MockServer",
    "NotifyEndpoint",
    "PlatformConfig",
    "PricingEntry",
    "RunDefaults",
    "TargetConfig",
    "WebConfig",
    "XcuitestConfig",
    "android_package",
    "ios_bundle_id",
    "load_config",
    "parse_config_dict",
    "require_android",
    "require_ios",
    "require_web",
    "resolve",
    "web_base_url",
    "web_engine",
    "xcuitest_targets_real_device",
]
