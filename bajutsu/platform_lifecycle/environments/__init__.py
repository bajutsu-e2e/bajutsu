"""One module per concrete `Environment` implementer (iOS, Android, web, XCUITest, fake).

Import a concrete environment from its own submodule (`environments.xcuitest`), or the
`environment_for` factory from the parent package (`platform_lifecycle`).

This package root re-exports only the wheel-bundled XCUITest runner probes
(`bundled_products_dir` / `bundled_runner_build_info`), so a caller outside this package — e.g. the
serve operations layer's `server_settings` (BE-0318) — reads them from here rather than reaching into
the private `_bundled_runner` submodule.
"""

from bajutsu.platform_lifecycle.environments._bundled_runner import (
    bundled_products_dir,
    bundled_runner_build_info,
)

__all__ = [
    "bundled_products_dir",
    "bundled_runner_build_info",
]
