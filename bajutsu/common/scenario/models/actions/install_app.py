"""The `installApp` action: install another device-group member's build mid-scenario (BE-0447)."""

from __future__ import annotations

from pydantic import Field

from bajutsu.common.scenario.models._base import _Model


class InstallApp(_Model):
    """Install the build of the device-group member `from` onto the device the step runs against.

    The scenario names a target, never a path: the build artifact stays in that target's config
    (`appPath`), so the scenario stays app-agnostic. `keepData` (default true) installs over an
    existing build of the same identifier and keeps its data container; false uninstalls that
    identifier first. The step launches nothing — a `foreground` addressed to the member does.
    """

    from_: str = Field(alias="from")
    keep_data: bool = Field(default=True, alias="keepData")
