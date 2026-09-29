"""The seam for a backend that can positively confirm the app under test has crashed (BE-0424)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AppCrashSignal(Protocol):
    """A backend that can positively confirm the app under test has crashed.

    A narrow opt-in, like `InterruptionPolicyTarget` and `SettledReadProvider` above, rather than a
    `Driver` member: `Driver` is `@runtime_checkable`, so every member it declares is required for
    `isinstance(x, base.Driver)` to answer `True` at all — a stub every backend and every narrower
    wrapper (`WebContextDriver`) would have to grow for a check this item never asks them. Only
    XCUITest and adb answer it today; the web backend is left to a follow-up item.
    """

    def app_crash_signal(self) -> str | None:
        """A short description of the app's crash, if this driver can confirm one right now.

        Called only once a step's own action, wait, or assertion has already failed — never polled
        proactively, which would add a round trip to every green run to catch a failure mode that is
        rare by construction. Answers `None` when the driver cannot tell "the app went down" from
        "the app is merely not showing what was expected".
        """
        ...
