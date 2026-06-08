"""simctl wrapper — erase / boot / launch / openurl / io.

Command builders are pure and unit-tested. Execution goes through an injectable
runner so the device-touching part stays thin and swappable in tests.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence

# (argv, extra_env) -> stdout
RunFn = Callable[[list[str], Mapping[str, str] | None], str]


def erase_cmd(udid: str) -> list[str]:
    return ["xcrun", "simctl", "erase", udid]


def boot_cmd(udid: str) -> list[str]:
    return ["xcrun", "simctl", "boot", udid]


def shutdown_cmd(udid: str) -> list[str]:
    return ["xcrun", "simctl", "shutdown", udid]


def launch_cmd(udid: str, bundle_id: str, args: Sequence[str] = ()) -> list[str]:
    return ["xcrun", "simctl", "launch", "--terminate-running-process", udid, bundle_id, *args]


def terminate_cmd(udid: str, bundle_id: str) -> list[str]:
    return ["xcrun", "simctl", "terminate", udid, bundle_id]


def openurl_cmd(udid: str, url: str) -> list[str]:
    return ["xcrun", "simctl", "openurl", udid, url]


def screenshot_cmd(udid: str, path: str) -> list[str]:
    return ["xcrun", "simctl", "io", udid, "screenshot", path]


def record_video_cmd(udid: str, path: str) -> list[str]:
    return ["xcrun", "simctl", "io", udid, "recordVideo", path]


def child_env(env: Mapping[str, str]) -> dict[str, str]:
    """Launch env vars are passed to the app via SIMCTL_CHILD_<NAME> on the parent process."""
    return {f"SIMCTL_CHILD_{k}": v for k, v in env.items()}


def list_booted_cmd() -> list[str]:
    return ["xcrun", "simctl", "list", "devices", "booted", "-j"]


def _real_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
    full_env = {**os.environ, **(extra_env or {})}
    return subprocess.run(
        args, capture_output=True, text=True, check=True, env=full_env
    ).stdout


def resolve_udid(udid: str, run: RunFn = _real_run) -> str:
    """Resolve the simctl alias "booted" to a concrete UDID.

    simctl accepts "booted", but the idb CLI requires a real
    UDID, so the run pipeline resolves it once up front. A concrete UDID passes
    through unchanged; "booted" picks the single booted device (the first if
    several). Falls back to "booted" if resolution fails (no booted device).
    """
    if udid != "booted":
        return udid
    try:
        data = json.loads(run(list_booted_cmd(), None))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return udid
    for devices in (data.get("devices") or {}).values():
        for dev in devices:
            if dev.get("state") == "Booted" and dev.get("udid"):
                return str(dev["udid"])
    return udid


class Env:
    """Thin simctl front end for one device."""

    def __init__(self, udid: str, run: RunFn = _real_run) -> None:
        self.udid = udid
        self._run = run

    def erase(self) -> None:
        self._run(erase_cmd(self.udid), None)

    def boot(self) -> None:
        try:
            self._run(boot_cmd(self.udid), None)
        except subprocess.CalledProcessError:
            pass  # already booted; boot is idempotent

    def launch(
        self,
        bundle_id: str,
        args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._run(launch_cmd(self.udid, bundle_id, args), child_env(env or {}))

    def terminate(self, bundle_id: str) -> None:
        try:
            self._run(terminate_cmd(self.udid, bundle_id), None)
        except subprocess.CalledProcessError:
            pass  # not running

    def openurl(self, url: str) -> None:
        self._run(openurl_cmd(self.udid, url), None)

    def screenshot(self, path: str) -> None:
        self._run(screenshot_cmd(self.udid, path), None)
