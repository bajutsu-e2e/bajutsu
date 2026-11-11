"""The resolved Android (adb) target knobs, present only on an android target (BE-0126)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AndroidConfig:
    """Android (adb) target knobs (BE-0126 / BE-0007). On `Effective` only when `platform` is android."""

    # Android target identifier (peer of iOS bundle_id / web base_url). "" when unset.
    package: str = ""
    # Built .apk to install on each device before launch (if missing). None = manual install.
    app_path: str | None = None
    # Shell command that builds `app_path`; `bajutsu serve` runs it on demand if the binary is
    # missing. None = no on-demand build.
    build: str | None = None
    # Runtime permissions granted up front (`pm grant`) at lease time, so a permission prompt never
    # blocks a scenario (BE-0210). Empty = grant nothing.
    grant_permissions: list[str] = field(default_factory=list)
    # Ask the resident server for each opted-in view's measured `View.getZ()` (BE-0355). Off by
    # default: the walk answering it runs over every node on every read and reports nothing for an
    # app that opted no view in (BE-0407 unit 18).
    native_z: bool = False
    # What a timed-out resident read tries before falling back to `uiautomator dump`: how many times
    # to read again on a fresh connection, and whether to restart the server after that.
    resident_read_retries: int = 1
    resident_restart: bool = True
    # `runsOn.android.locale`: the app's per-app language (BE-0478). None keeps the device's own,
    # which `Effective.locale` cannot say — it always holds the iOS default.
    locale: str | None = None
