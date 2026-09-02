"""The platform-neutral device-error base every backend shares (BE-0260).

`run` / `crawl` / `record` / `audit` and the doctor paths catch a device fault the same way
whatever backend raised it, so the base type lives in this leaf module rather than in the iOS
`simctl` backend. The iOS (`simctl.DeviceError`) and Android (`adb.DeviceError`) errors subclass it
as siblings, so a generic `except DeviceError` handler need not import an iOS backend module just to
name the exception it catches — keeping `bajutsu` backend-agnostic (platform is a backend).
"""

from __future__ import annotations


class DeviceError(RuntimeError):
    """A device operation failed in a way the user can act on.

    Carries a clean, actionable message (a bad udid/serial, an app that isn't installed, a wedged
    simulator or browser) — the CLI surfaces it and exits 2, instead of dumping a Python traceback.
    Each backend subclasses it for platform-specific detail; a generic handler catches this base.
    """
