"""The `setLocation` action: override the device's GPS position."""

from __future__ import annotations

from pydantic import Field

from bajutsu.common.scenario.models._base import _Model


class SetLocation(_Model):
    """Override the device's GPS location.

    iOS sets it through the XCUITest runner or simctl; Android through the emulator console or an
    in-app mock. The bounds reject an impossible coordinate when the scenario loads, rather than as a backend's
    own error mid-run (BE-0475).
    """

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
