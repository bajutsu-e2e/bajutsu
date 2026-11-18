"""The `setLocation` action: override the device's GPS position."""

from __future__ import annotations

from bajutsu.common.scenario.models._base import _Model


class SetLocation(_Model):
    """Override the device's GPS location (simctl on iOS; the console or an in-app mock on Android)."""

    lat: float
    lon: float
