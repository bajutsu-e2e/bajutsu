"""The error raised when the runner build predates a route the host called (BE-0475)."""

from __future__ import annotations

from .xcuitest_channel_error import XcuitestChannelError


class XcuitestRouteMissingError(XcuitestChannelError):
    """The runner answered `unknown endpoint`: its build predates the route, and a rebuild adds it.

    Kept distinct from other channel errors so a caller with a fallback for the route (a Simulator's
    `simctl location set`) can take it, as it would for `unsupported`, while any other runner fault
    still fails the step.
    """
