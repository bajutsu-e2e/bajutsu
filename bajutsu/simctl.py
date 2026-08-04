"""simctl wrapper — erase / boot / launch / openurl / io.

Command builders are pure and unit-tested. Execution goes through an injectable
runner so the device-touching part stays thin and swappable in tests.
"""

from __future__ import annotations

import contextlib
import json
import os
import plistlib
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence

from bajutsu import device_errors
from bajutsu.device_id import is_valid_device_id

# (argv, extra_env) -> stdout
RunFn = Callable[[list[str], Mapping[str, str] | None], str]


class DeviceError(device_errors.DeviceError):
    """A simctl operation failed in a way the user can act on (e.g. launching an
    app that isn't installed, or an invalid device).

    The iOS-specific subclass of the platform-neutral `device_errors.DeviceError` (BE-0260): a
    generic handler catches the base, iOS-only code catches this. Carries a clean, actionable
    message — the CLI surfaces it and exits 2, instead of dumping a Python traceback.
    """


def device_error(exc: subprocess.CalledProcessError) -> DeviceError:
    """Turn a raw simctl failure into a clean DeviceError.

    Keeps the failed command, simctl's exit code, and its stderr — usually the
    most actionable part, e.g. "Unable to ... in current state: Booted" or
    "Unable to lookup in current state" when the app isn't installed.
    """
    cmd = exc.cmd if isinstance(exc.cmd, str) else " ".join(map(str, exc.cmd or []))
    detail = (exc.stderr if isinstance(exc.stderr, str) else "") or ""
    msg = f"device operation failed (exit {exc.returncode}): {cmd}"
    detail = detail.strip()
    return DeviceError(f"{msg}\n{detail}" if detail else msg)


def validated_udid(udid: str) -> str:
    """Return `udid` if it is safe to place on an `xcrun simctl` argv, else raise.

    The shared entry point for the simctl family of argv builders — this module's own builders,
    plus the simctl argv assembled in `intervals.py` (evidence capture) and
    `platform_lifecycle.environments.xcuitest` (the xcodebuild destination). Public (unlike adb's
    per-module `_checked_serial`) precisely
    because that argv-building is spread across modules. The check is the shared `device_id` policy — chiefly that an id never leads with `-`,
    which simctl would read as an option (argv option injection from an untrusted `--udid` / config).

    Raises:
        DeviceError: if `udid` violates the policy — so a bad `--udid` surfaces as the CLI's clean
            exit-2 device fault, the same boundary adb's `_checked_serial` uses.
    """
    if is_valid_device_id(udid):
        return udid
    raise DeviceError(f"invalid udid: {udid!r}")


def erase_cmd(udid: str) -> list[str]:
    return ["xcrun", "simctl", "erase", validated_udid(udid)]


def boot_cmd(udid: str) -> list[str]:
    return ["xcrun", "simctl", "boot", validated_udid(udid)]


def shutdown_cmd(udid: str) -> list[str]:
    return ["xcrun", "simctl", "shutdown", validated_udid(udid)]


def launch_cmd(udid: str, bundle_id: str, args: Sequence[str] = ()) -> list[str]:
    return [
        "xcrun",
        "simctl",
        "launch",
        "--terminate-running-process",
        validated_udid(udid),
        bundle_id,
        *args,
    ]


# A locale is config-supplied, so it reaches an argv the same way a `--udid` does; the same policy
# applies (chiefly: never leads with `-`, which `defaults` would read as an option). Deliberately
# permissive about the body so an ICU keyword form (`en_US@calendar=japanese`) still passes.
_LOCALE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_@=.+-]*$")


def validated_locale(locale: str) -> str:
    """Return `locale` if it is safe to place on a `defaults` argv, else raise.

    Raises:
        DeviceError: if `locale` violates the policy — the same clean exit-2 device fault a bad
            `--udid` surfaces as.
    """
    if _LOCALE_RE.match(locale):
        return locale
    raise DeviceError(f"invalid locale: {locale!r}")


def language_of(locale: str) -> str:
    """The language subtag a locale resolves to (`ja_JP` -> `ja`).

    The one place the split lives, so the app's own `-AppleLanguages` launch argument and the
    Simulator's system-wide language (BE-0320) can never name different languages. Splits on either
    separator, since `_LOCALE_RE` admits the hyphenated tag form (`en-US`) as well as the
    underscored one the config examples use — a `-` there would otherwise leave the whole tag as
    the "language" and never match anything.
    """
    return re.split(r"[_-]", locale, maxsplit=1)[0]


def locale_args(locale: str) -> list[str]:
    """App launch arguments that force the locale + language. iOS reads `-AppleLocale` and
    `-AppleLanguages` from the process argv via NSUserDefaults, so passing them as the app's
    launch args makes a run deterministic regardless of the device's region settings.
    `ja_JP` -> `-AppleLocale ja_JP -AppleLanguages (ja)`.

    These reach the app process alone. SpringBoard — which owns the permission prompts
    `handleSystemAlert` taps — is a separate process Bajutsu never launches, so its own language
    comes from the device's global preference domain instead (`system_locale_cmds`, BE-0320)."""
    return ["-AppleLocale", locale, "-AppleLanguages", f"({language_of(locale)})"]


# The Simulator's global preference domain, where its system-wide language and locale live. Writing
# it needs a booted device (`simctl spawn` runs the guest's own `defaults`), which is why the
# BE-0320 pin happens after `boot` rather than as a launch argument.
_GLOBAL_DOMAIN = "-globalDomain"

# The two global-domain keys that decide which language SpringBoard renders in. `AppleLanguages` is
# written as a one-element array (not appended to) so the pinned language is the device's first
# choice with nothing behind it to fall back to — the same single value `locale_args` gives the app.
_LANGUAGES_KEY = "AppleLanguages"
_LOCALE_KEY = "AppleLocale"


def export_globals_cmd(udid: str) -> list[str]:
    """`simctl spawn <udid> defaults export -globalDomain -` — the device's global domain as an XML plist.

    `defaults export` rather than `defaults read`: its output is a plist `plistlib` parses exactly,
    where `read`'s is a human-readable rendering that would need hand-parsing.
    """
    return [
        "xcrun",
        "simctl",
        "spawn",
        validated_udid(udid),
        "defaults",
        "export",
        _GLOBAL_DOMAIN,
        "-",
    ]


def system_locale_cmds(udid: str, locale: str) -> list[list[str]]:
    """The `defaults write` argvs that pin the Simulator's system-wide language and locale (BE-0320)."""
    checked_udid, checked_locale = validated_udid(udid), validated_locale(locale)
    spawn = ["xcrun", "simctl", "spawn", checked_udid, "defaults", "write", _GLOBAL_DOMAIN]
    return [
        [*spawn, _LANGUAGES_KEY, "-array", language_of(checked_locale)],
        [*spawn, _LOCALE_KEY, "-string", checked_locale],
    ]


def terminate_cmd(udid: str, bundle_id: str) -> list[str]:
    return ["xcrun", "simctl", "terminate", validated_udid(udid), bundle_id]


def openurl_cmd(udid: str, url: str) -> list[str]:
    return ["xcrun", "simctl", "openurl", validated_udid(udid), url]


def screenshot_cmd(udid: str, path: str) -> list[str]:
    return ["xcrun", "simctl", "io", validated_udid(udid), "screenshot", path]


def record_video_cmd(udid: str, path: str) -> list[str]:
    return ["xcrun", "simctl", "io", validated_udid(udid), "recordVideo", path]


def set_location_cmd(udid: str, lat: float, lon: float) -> list[str]:
    return ["xcrun", "simctl", "location", validated_udid(udid), "set", f"{lat},{lon}"]


def clear_location_cmd(udid: str) -> list[str]:
    return ["xcrun", "simctl", "location", validated_udid(udid), "clear"]


# The one permission-vocabulary service (BE-0276) with no simctl privacy TCC (Transparency,
# Consent, and Control) equivalent — iOS notification authorization is not part of TCC. Every other
# vocabulary entry names its own TCC service (`base.PERMISSION_SERVICES`'s spelling matches
# `simctl privacy`'s service names 1:1), so no separate service->TCC-name map is needed.
_NO_TCC_SERVICE = "notifications"

# simctl's host<->Simulator pasteboard sync (`pbcopy`) intermittently times out — simctl
# exits 60 (ETIMEDOUT), or the call hangs past a reasonable bound — which is transient: a
# re-run clears it. Retry a bounded number of times so a genuine fault still surfaces. The
# budget is deliberately generous (linear backoff, ~15s over five attempts): CI has been
# seen wedged past three quick tries (~1.5s), and this recovery path is only paid when a
# timeout actually occurs, so widening it costs nothing on the healthy path.
_PBCOPY_MAX_ATTEMPTS = 5
_PBCOPY_RETRY_DELAY_S = 1.5
_PBCOPY_TIMEOUT_S = 30.0
_PBCOPY_TIMEOUT_EXIT = 60  # simctl's ETIMEDOUT — the one transient exit worth retrying


def privacy_cmd(udid: str, action: str, tcc_service: str, bundle_id: str) -> list[str]:
    """`simctl privacy <udid> <grant|revoke> <tcc-service> <bundle>` (BE-0276)."""
    return ["xcrun", "simctl", "privacy", validated_udid(udid), action, tcc_service, bundle_id]


def push_cmd(udid: str, bundle_id: str, payload_path: str) -> list[str]:
    return ["xcrun", "simctl", "push", validated_udid(udid), bundle_id, payload_path]


def keychain_reset_cmd(udid: str) -> list[str]:
    return ["xcrun", "simctl", "keychain", validated_udid(udid), "reset"]


def pbcopy_cmd(udid: str) -> list[str]:
    """Write to the pasteboard via simctl pbcopy (text comes from stdin; empty stdin clears it)."""
    return ["xcrun", "simctl", "pbcopy", validated_udid(udid)]


def pbpaste_cmd(udid: str) -> list[str]:
    """Read the pasteboard via simctl pbpaste (the content comes back on stdout)."""
    return ["xcrun", "simctl", "pbpaste", validated_udid(udid)]


def home_cmd(udid: str) -> list[str]:
    """Send the foreground app to the background, as pressing the Home button does.

    simctl has no Home-button command (`simctl ui` only sets appearance/contrast/content-size),
    so bring SpringBoard — the home screen — to the front instead. It backgrounds the app
    *without* terminating it, so the app's state survives and `foreground` can resume the same
    process.
    """
    return ["xcrun", "simctl", "launch", validated_udid(udid), "com.apple.springboard"]


def foreground_cmd(udid: str, bundle_id: str) -> list[str]:
    """Resume a backgrounded app to the foreground (simctl launch, without
    --terminate-running-process, so the running process is brought forward rather than relaunched)."""
    return ["xcrun", "simctl", "launch", validated_udid(udid), bundle_id]


def status_bar_override_cmd(udid: str, **kwargs: str | int) -> list[str]:
    """Override status bar fields. Supported keys (snake_case): time, battery_level,
    battery_state, cellular_bars, wifi_bars."""
    cmd = ["xcrun", "simctl", "status_bar", validated_udid(udid), "override"]
    key_map = {
        "time": "--time",
        "battery_level": "--batteryLevel",
        "battery_state": "--batteryState",
        "cellular_bars": "--cellularBars",
        "wifi_bars": "--wifiBars",
    }
    for key, flag in key_map.items():
        val = kwargs.get(key)
        if val is not None:
            cmd.extend([flag, str(val)])
    return cmd


def status_bar_clear_cmd(udid: str) -> list[str]:
    return ["xcrun", "simctl", "status_bar", validated_udid(udid), "clear"]


def install_cmd(udid: str, app_path: str) -> list[str]:
    return ["xcrun", "simctl", "install", validated_udid(udid), app_path]


def uninstall_cmd(udid: str, bundle_id: str) -> list[str]:
    return ["xcrun", "simctl", "uninstall", validated_udid(udid), bundle_id]


def get_app_container_cmd(udid: str, bundle_id: str) -> list[str]:
    """Path of the app's installed bundle — succeeds only if the app is installed."""
    return ["xcrun", "simctl", "get_app_container", validated_udid(udid), bundle_id, "app"]


def data_container_cmd(udid: str, bundle_id: str) -> list[str]:
    """Path of the app's data container (its sandbox home) — succeeds only if the app is installed."""
    return ["xcrun", "simctl", "get_app_container", validated_udid(udid), bundle_id, "data"]


def child_env(env: Mapping[str, str]) -> dict[str, str]:
    """Launch env vars are passed to the app via SIMCTL_CHILD_<NAME> on the parent process."""
    return {f"SIMCTL_CHILD_{k}": v for k, v in env.items()}


def list_booted_cmd() -> list[str]:
    return ["xcrun", "simctl", "list", "devices", "booted", "-j"]


def list_devices_cmd() -> list[str]:
    return ["xcrun", "simctl", "list", "devices", "available", "-j"]


def list_all_devices_cmd() -> list[str]:
    """Every device simctl knows, including the unavailable ones `list_devices_cmd` filters out.

    An unavailable device still carries its `deviceTypeIdentifier`, the type a replacement is cloned
    from; the available-only listing would hide exactly the device whose type we need.
    """
    return ["xcrun", "simctl", "list", "devices", "-j"]


def list_devicetypes_cmd() -> list[str]:
    return ["xcrun", "simctl", "list", "devicetypes", "-j"]


def create_cmd(name: str, device_type: str) -> list[str]:
    """Create a device of `device_type`, letting simctl pair the newest compatible runtime.

    Naming no runtime is deliberate: pinning one that the host has since dropped is what makes a
    replacement fail on the very host degradation it exists to recover from.
    """
    return [
        "xcrun",
        "simctl",
        "create",
        validated_device_arg(name),
        validated_device_arg(device_type),
    ]


def bootstatus_cmd(udid: str) -> list[str]:
    """Boot the device if it isn't already (-b) and wait until it finishes booting."""
    return ["xcrun", "simctl", "bootstatus", validated_udid(udid), "-b"]


def validated_device_arg(value: str) -> str:
    """Return `value` if it is safe to place on a simctl argv, else raise.

    A device type identifier and a device name reach an argv the way a `--udid` does — one comes
    from config, the other from simctl's own listing — so they take the same never-leads-with-`-`
    policy `validated_udid` applies, while staying permissive about the body (a type identifier is
    dotted, a name has spaces).

    Raises:
        DeviceError: if `value` is empty or would read as a simctl option.
    """
    if value and not value.startswith("-"):
        return value
    raise DeviceError(f"invalid simctl device argument: {value!r}")


def _real_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
    full_env = {**os.environ, **(extra_env or {})}
    return subprocess.run(args, capture_output=True, text=True, check=True, env=full_env).stdout


def resolve_udid(udid: str, run: RunFn = _real_run) -> str:
    """Resolve the simctl alias "booted" to a concrete UDID.

    simctl accepts "booted", but downstream steps need a concrete
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


def booted_udids(run: RunFn = _real_run) -> list[str]:
    """UDIDs of the currently-booted Simulators (empty on any failure)."""
    try:
        data = json.loads(run(list_booted_cmd(), None))
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return []
    return [
        str(dev["udid"])
        for devices in (data.get("devices") or {}).values()
        for dev in devices
        if dev.get("state") == "Booted" and dev.get("udid")
    ]


def runtime_label(runtime_id: str) -> str:
    """'com.apple.CoreSimulator.SimRuntime.iOS-26-5' -> 'iOS 26.5'."""
    return runtime_id.split("SimRuntime.")[-1].replace("-", " ", 1).replace("-", ".")


def device_type_label(device_type: str) -> str:
    """'com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro' -> 'iPhone 17 Pro'.

    A created device's *name* is free-form, but two consumers read it as the human model: the report's
    device row and `serve`'s capability inventory, which takes the `iphone` / `ipad` class token by
    substring. So a device this code names has to carry its model, which this recovers from the type
    identifier rather than paying another `simctl list devicetypes` to look the name up.
    """
    return device_type.rsplit(".", 1)[-1].replace("-", " ")


def device_catalog(run: RunFn = _real_run) -> dict[str, dict[str, str]]:
    """Map udid -> {'name', 'runtime'} for the available simulators (best-effort, {} on any
    failure). Lets a run label which simulator (device model + OS) each scenario ran on."""
    try:
        data = json.loads(run(list_devices_cmd(), None))
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError):
        return {}
    catalog: dict[str, dict[str, str]] = {}
    for runtime, devices in (data.get("devices") or {}).items():
        label = runtime_label(runtime)
        for dev in devices:
            udid = dev.get("udid")
            if udid:
                catalog[str(udid)] = {"name": str(dev.get("name", "")), "runtime": label}
    return catalog


def device_available(udid: str, run: RunFn = _real_run) -> bool | None:
    """Whether simctl still lists `udid` as available, or None when the probe itself failed.

    The three-valued result is the point: device recovery creates a replacement only on a definite
    `False`. A wedged CoreSimulator makes the listing itself fail, and reading that as "the device
    is gone" would create a replacement on a host that cannot boot one.
    """
    try:
        data = json.loads(run(list_devices_cmd(), None))
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError):
        return None
    return any(
        dev.get("udid") == udid
        for devices in (data.get("devices") or {}).values()
        for dev in devices
    )


def device_type_of(udid: str, run: RunFn = _real_run) -> str | None:
    """The device's `deviceTypeIdentifier` (None when unresolvable), so a replacement can clone it.

    Reads the unfiltered listing: this is captured while the device is healthy, but a device that
    has become *unavailable* rather than deleted still answers here, which keeps the clone possible
    in the case that matters.
    """
    try:
        data = json.loads(run(list_all_devices_cmd(), None))
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError):
        return None
    for devices in (data.get("devices") or {}).values():
        for dev in devices:
            if dev.get("udid") == udid and dev.get("deviceTypeIdentifier"):
                return str(dev["deviceTypeIdentifier"])
    return None


def device_type_identifier(name: str, run: RunFn = _real_run) -> str | None:
    """The devicetype identifier a human device name resolves to ('iPhone 17 Pro' -> com.apple…).

    None when this host's Xcode ships no such type — the caller then falls back rather than failing,
    since a config default outliving the Xcode that had it is ordinary.
    """
    try:
        data = json.loads(run(list_devicetypes_cmd(), None))
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError):
        return None
    for dev_type in data.get("devicetypes") or []:
        if dev_type.get("name") == name and dev_type.get("identifier"):
            return str(dev_type["identifier"])
    return None


def newest_iphone_device_type(run: RunFn = _real_run) -> str | None:
    """The last iPhone devicetype simctl lists (None when it lists none).

    simctl orders devicetypes oldest to newest, so the last iPhone is the newest one installed —
    the same "whichever iPhone this host actually has" choice the CI boot action makes when no
    model is pinned.
    """
    try:
        data = json.loads(run(list_devicetypes_cmd(), None))
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError):
        return None
    iphones = [
        str(d["identifier"])
        for d in data.get("devicetypes") or []
        if d.get("productFamily") == "iPhone" and d.get("identifier")
    ]
    return iphones[-1] if iphones else None


def create_device(
    device_type: str, run: RunFn = _real_run, *, name: str = "bajutsu-recovered"
) -> str:
    """Create a Simulator of `device_type` and return its udid.

    Raises:
        DeviceError: if simctl could not create the device — chiefly a host whose iOS runtimes have
            gone with the device we are replacing, where no replacement is possible at all.
    """
    try:
        out = run(create_cmd(name, device_type), None)
    except subprocess.CalledProcessError as exc:
        raise device_error(exc) from exc
    except OSError as exc:
        raise DeviceError(f"could not create a replacement Simulator: {exc}") from exc
    udid = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if not udid:
        raise DeviceError(f"simctl create {device_type} printed no udid")
    return validated_udid(udid)


class Env:
    """Thin simctl front end for one device."""

    def __init__(self, udid: str, run: RunFn = _real_run) -> None:
        # Validate at construction so a bad --udid fails fast at the object boundary (the builders
        # below also validate, so this is belt-and-suspenders — the same posture the device drivers
        # take for their own udid).
        self.udid = validated_udid(udid)
        self._run = run

    def erase(self) -> None:
        self._run(erase_cmd(self.udid), None)

    def shutdown(self) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(shutdown_cmd(self.udid), None)

    def boot(self) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(boot_cmd(self.udid), None)

    def system_locale_matches(self, locale: str) -> bool | None:
        """Whether the device's global domain already renders the language `pin_system_locale` writes.

        `None` distinguishes "could not read the domain" from a definite mismatch, so a caller can
        act on what it actually observed: skipping the write needs a positive match, while failing
        the run needs a positive *mis*match — an unreadable device is neither. A domain that reads
        back fine but carries no pinned language is a *mismatch*, not an unknown: it is positive
        evidence that nothing pinned it.

        A match is one language with **nothing queued behind it** whose subtag is the one we would
        write, plus an exact `AppleLocale`. Comparing the subtag rather than the whole entry matters
        for the common case: a freshly created Simulator inherits the host's language-region tag
        (`en-US`), which selects the same language as the bare `en` this writes, so an exact string
        comparison would rewrite and reboot every device that was already right. A second language
        behind the first is still a mismatch — SpringBoard can fall back to it for a string the first
        lacks, which is the "matched by accident" behaviour this exists to remove.

        Raises:
            DeviceError: `locale` is not safe to place on a `defaults` argv — checked up front, so a
                malformed one never costs a subprocess round trip first.
        """
        checked = validated_locale(locale)
        try:
            exported = plistlib.loads(self._run(export_globals_cmd(self.udid), None).encode())
        except (subprocess.CalledProcessError, plistlib.InvalidFileException, ValueError):
            return None
        if not isinstance(exported, dict):
            return None
        languages = exported.get(_LANGUAGES_KEY)
        if not isinstance(languages, list) or len(languages) != 1:
            return False  # absent, or a fallback queued behind the first
        return (
            language_of(str(languages[0])) == language_of(checked)
            and exported.get(_LOCALE_KEY) == checked
        )

    def pin_system_locale(self, locale: str) -> bool:
        """Write the device's system-wide language and locale unless already exact; True if it wrote.

        The caller reboots the Simulator when this returns True — a running SpringBoard does not pick
        a global-domain write up live (BE-0320). Skipping the write on a device that already carries
        the value is what keeps the common case (a Simulator pinned by an earlier spawn, or already
        on the configured locale) at the cost of one read instead of a second boot cycle. Only a
        positive match skips the write; an unreadable domain (`None`) writes, since nothing was
        observed to already be right.
        """
        if self.system_locale_matches(locale) is True:
            return False
        for cmd in system_locale_cmds(self.udid, locale):
            self._run(cmd, None)
        return True

    def is_installed(self, bundle_id: str) -> bool:
        try:
            self._run(get_app_container_cmd(self.udid, bundle_id), None)
            return True
        except subprocess.CalledProcessError:
            return False

    def install(self, app_path: str) -> None:
        self._run(install_cmd(self.udid, app_path), None)

    def uninstall(self, bundle_id: str) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(uninstall_cmd(self.udid, bundle_id), None)

    def launch(
        self,
        bundle_id: str,
        args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._run(launch_cmd(self.udid, bundle_id, args), child_env(env or {}))

    def terminate(self, bundle_id: str) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(terminate_cmd(self.udid, bundle_id), None)

    def openurl(self, url: str) -> None:
        self._run(openurl_cmd(self.udid, url), None)

    def screenshot(self, path: str) -> None:
        self._run(screenshot_cmd(self.udid, path), None)

    def set_location(self, lat: float, lon: float) -> None:
        self._run(set_location_cmd(self.udid, lat, lon), None)

    def clear_location(self) -> None:
        self._run(clear_location_cmd(self.udid), None)

    def apply_permissions(self, bundle_id: str, permissions: Mapping[str, str]) -> None:
        """Grant or revoke each `service: grant|revoke` entry in `permissions` up front, so a
        runtime prompt never blocks the run (`simctl privacy`, BE-0276).

        Every entry's service and action are validated before any `simctl privacy` call runs, so
        an unsupported service or an unrecognized action fails before the device is touched at all
        — never partway through, leaving some services already mutated (preflight/schema normally
        reject this before any device work; this validation is the runtime backstop for a caller
        that bypasses both).

        Raises:
            DeviceError: a service has no TCC equivalent (`notifications`), or an action is neither
                `grant` nor `revoke`.
        """
        for service, action in permissions.items():
            if service == _NO_TCC_SERVICE:
                raise DeviceError(f"permissions.{service} has no simctl privacy equivalent on iOS")
            if action not in ("grant", "revoke"):
                raise DeviceError(
                    f"unknown simctl privacy action: {action!r} (expected grant|revoke)"
                )
        for service, action in permissions.items():
            self._run(privacy_cmd(self.udid, action, service, bundle_id), None)

    def clear_keychain(self) -> None:
        self._run(keychain_reset_cmd(self.udid), None)

    def clear_clipboard(self) -> None:
        # pbcopy reads from stdin, which RunFn doesn't support. Use subprocess
        # directly but route through a class-level attribute so tests can patch it.
        self._run_pbcopy(pbcopy_cmd(self.udid))

    def set_clipboard(self, text: str) -> None:
        # Same simctl pbcopy as clearing, but with the seed text on stdin.
        self._run_pbcopy(pbcopy_cmd(self.udid), text)

    @staticmethod
    def _run_pbcopy(cmd: list[str], text: str = "") -> None:
        # pbcopy is idempotent — re-feeding the same stdin is safe — so retry the transient
        # simctl pasteboard timeout (see `_PBCOPY_*`) rather than fail the whole scenario on it.
        last: subprocess.CalledProcessError | subprocess.TimeoutExpired | None = None
        for attempt in range(_PBCOPY_MAX_ATTEMPTS):
            try:
                subprocess.run(
                    cmd,
                    input=text,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=_PBCOPY_TIMEOUT_S,
                )
                return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                last = exc
                # Only the transient exit-60 timeout (and a Python-side hang, which has no
                # returncode) is worth retrying; a genuine simctl failure — an un-booted device,
                # a bad UDID — won't clear on a re-run, so surface it now rather than after the
                # full backoff budget.
                if (
                    isinstance(exc, subprocess.CalledProcessError)
                    and exc.returncode != _PBCOPY_TIMEOUT_EXIT
                ):
                    raise
                if attempt + 1 < _PBCOPY_MAX_ATTEMPTS:
                    time.sleep(_PBCOPY_RETRY_DELAY_S * (attempt + 1))
        assert last is not None  # the loop runs at least once, so a failure sets `last`
        raise last

    def get_clipboard(self) -> str:
        # pbpaste returns the pasteboard content on stdout; RunFn already yields stdout.
        return self._run(pbpaste_cmd(self.udid), None)

    def home(self) -> None:
        self._run(home_cmd(self.udid), None)

    def foreground(self, bundle_id: str) -> None:
        self._run(foreground_cmd(self.udid, bundle_id), None)

    def override_status_bar(self, **kwargs: str | int) -> None:
        self._run(status_bar_override_cmd(self.udid, **kwargs), None)

    def clear_status_bar(self) -> None:
        self._run(status_bar_clear_cmd(self.udid), None)

    def push(self, bundle_id: str, payload: dict[str, object]) -> None:
        """Deliver a simulated push: write the APNs payload to a temp file, then push it."""
        with tempfile.NamedTemporaryFile("w", suffix=".apns", delete=False, encoding="utf-8") as f:
            json.dump(payload, f)
            path = f.name
        try:
            self._run(push_cmd(self.udid, bundle_id, path), None)
        finally:
            os.unlink(path)
