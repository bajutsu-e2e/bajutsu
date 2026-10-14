"""The resolved iOS (XCUITest) target knobs, present only on an ios target (BE-0126)."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.config.schema import XcuitestConfig


@dataclass(frozen=True)
class IosConfig:
    """iOS (XCUITest) target knobs (BE-0126). On `Effective` only when `platform == "ios"`."""

    bundle_id: str = ""
    # Built .app to install on each device before launch (if missing). None = manual install.
    app_path: str | None = None
    # Shell command that builds `app_path`; `bajutsu serve` runs it on demand if the binary is
    # missing. None = no on-demand build.
    build: str | None = None
    # XCUITest runner config (BE-0019), built from `driver.runner` and `runsOn.kind` (BE-0452).
    xcuitest: XcuitestConfig | None = None
