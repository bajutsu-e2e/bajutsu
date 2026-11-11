"""The command shapes each install method's remedy is rendered from."""

from __future__ import annotations

from .brew import Brew
from .extra import Extra
from .manual import Manual
from .playwright import Playwright
from .requirement import Requirement
from .tool import Tool

# How a piece is obtained when missing. The installer dispatches on the concrete type; `preflight`
# renders it into a remedy line via `remedy`.
InstallMethod = Extra | Brew | Playwright | Manual


# Backend actuator (bajutsu.common.backends actuator names) -> what it needs. `xcuitest` (iOS, BE-0290 —
# the sole iOS backend) needs Xcode's `xcodebuild`. The web browser is engine-specific, so it is not
# listed here (see `playwright_browser`). `adb` (Android, BE-0007) needs the platform-tools `adb`
# binary; the emulator is only needed to *boot* an AVD, not to drive a running device, so it is not
# listed. Its `android` extra carries the WebSocket client a `web:` block's CDP source needs (BE-0482).
# `fake` needs nothing.
BACKENDS: dict[str, Requirement] = {
    "adb": Requirement(extra="android", tools=(Tool("adb", Brew("android-platform-tools")),)),
    "playwright": Requirement(extra="web"),
    "xcuitest": Requirement(
        tools=(Tool("xcodebuild", Manual("Xcode — `xcode-select --install`")),)
    ),
    "fake": Requirement(),
}

# Optional capability -> its pip extra (BE-0111 / BE-0048 / BE-0017). Centralized here for one
# source of truth; the installer wires the `ai` extra from a config's AI provider, the others are
# available for an explicit request.
CAPABILITIES: dict[str, Requirement] = {
    "ai": Requirement(extra="ai"),
    "visual": Requirement(extra="visual"),
    "mcp": Requirement(extra="mcp"),
}
