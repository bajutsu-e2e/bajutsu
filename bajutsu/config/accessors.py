"""Platform-narrowing accessors for a resolved `Effective` (BE-0126).

`require_*` narrow the `platform_config` union for code already committed to one platform (failing
loudly otherwise); the "soft" getters read a platform-specific value defensively across platforms,
returning a safe default off-platform. One definition each, shared by every layer, instead of an
inline `isinstance` at each call site.
"""

from __future__ import annotations

from bajutsu.config.effective import AndroidConfig, Effective, IosConfig, WebConfig


def require_ios(eff: Effective) -> IosConfig:
    """The iOS sub-config, narrowed for the type checker, or a loud failure (BE-0126).

    For code already committed to an iOS-only path (the iOS/XCUITest environment, the idb doctor
    probe): it narrows the platform union to `IosConfig` and fails fast rather than silently reading
    a default if a non-iOS target ever reaches it. Code that has *not* committed to a platform must
    narrow with `isinstance` / `match` instead — reading a platform's knobs off `platform_config`
    without narrowing is a type error, which is the point of the split.
    """
    cfg = eff.platform_config
    if not isinstance(cfg, IosConfig):
        raise TypeError(f"target {eff.target!r} is not an iOS target (platform {eff.platform})")
    return cfg


def require_web(eff: Effective) -> WebConfig:
    """The web sub-config, narrowed for the type checker, or a loud failure (BE-0126).

    The web counterpart of `require_ios`, for code already on a web-only path (the web environment,
    the Playwright doctor probe).
    """
    cfg = eff.platform_config
    if not isinstance(cfg, WebConfig):
        raise TypeError(f"target {eff.target!r} is not a web target (platform {eff.platform})")
    return cfg


def require_android(eff: Effective) -> AndroidConfig:
    """The Android sub-config, narrowed for the type checker, or a loud failure (BE-0126 / BE-0007).

    The Android counterpart of `require_ios` / `require_web`, for code already on an adb-only path
    (the `AndroidEnvironment` lifecycle).
    """
    cfg = eff.platform_config
    if not isinstance(cfg, AndroidConfig):
        raise TypeError(f"target {eff.target!r} is not an Android target (platform {eff.platform})")
    return cfg


# "Soft" per-platform accessors (BE-0126): the platform's knob, or a safe default for another
# platform. Unlike `require_ios` / `require_web`, these don't fail — they're for code that reads a
# platform-specific value defensively across platforms (a common-core field whose meaningful value
# only exists on one platform, e.g. launchServer's baseUrl probe; or a config gate that inspects
# every platform's handle). One definition each, shared by every layer, instead of an inline
# `isinstance` at each call site.
def web_base_url(eff: Effective) -> str | None:
    """The web target's base URL, or None for a non-web target."""
    return eff.platform_config.base_url if isinstance(eff.platform_config, WebConfig) else None


def web_engine(eff: Effective) -> str:
    """The web target's rendering engine, or the chromium default for a non-web target."""
    return eff.platform_config.browser if isinstance(eff.platform_config, WebConfig) else "chromium"


def ios_bundle_id(eff: Effective) -> str:
    """The iOS target's bundle id, or "" for a non-iOS target."""
    return eff.platform_config.bundle_id if isinstance(eff.platform_config, IosConfig) else ""


def android_package(eff: Effective) -> str:
    """The Android target's package, or "" for a non-Android target."""
    return eff.platform_config.package if isinstance(eff.platform_config, AndroidConfig) else ""


def xcuitest_targets_real_device(eff: Effective) -> bool:
    """True when the target drives a real iOS device via XCUITest (`xcuitest.deviceType: device`).

    The Simulator default — and every non-iOS target — is False. Consulted by the capability
    narrowing that drops the simctl-backed DeviceControl / permission tokens on a real device
    (BE-0238): simctl reaches only the Simulator, so those capabilities do not apply on a physical
    device.
    """
    cfg = eff.platform_config
    return (
        isinstance(cfg, IosConfig)
        and cfg.xcuitest is not None
        and cfg.xcuitest.device_type == "device"
    )
