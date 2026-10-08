"""The one error family a device runner build or lookup raises (BE-0456)."""

from __future__ import annotations


class DeviceRunnerError(Exception):
    """A device runner cannot be built or resolved; the message names what is missing and its fix."""
