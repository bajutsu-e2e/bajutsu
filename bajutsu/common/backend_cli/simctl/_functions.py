"""Drive one Simulator through the simctl command line, under a deadline on every call."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Mapping, Sequence

from bajutsu.common.devices.id import is_valid_device_id

from ._shared import _LANGUAGES_KEY, _LOCALE_KEY, RunFn
from .device_error import DeviceError
from .device_timeout import DeviceTimeout

_logger = logging.getLogger(__name__)


# A locale is config-supplied, so it reaches an argv the same way a `--udid` does; the same policy
# applies (chiefly: never leads with `-`, which `defaults` would read as an option). Deliberately
# permissive about the body so an ICU keyword form (`en_US@calendar=japanese`) still passes.
_LOCALE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_@=.+-]*$")


# The Simulator's global preference domain, where its system-wide language and locale live. Writing
# it needs a booted device (`simctl spawn` runs the guest's own `defaults`), which is why the
# BE-0320 pin happens after `boot` rather than as a launch argument.
_GLOBAL_DOMAIN = "-globalDomain"


# Every simctl call that goes through `real_run` carries a deadline (BE-0363), so a wedged
# CoreSimulator surfaces as a named device fault rather than hanging until CI cancels the whole job
# — a cancelled job names no cause at all. One value cannot serve every command, which is why there
# are two below and why the helper picks between them from the command itself: `bootstatus` waits
# out a full boot, while `list` returns in well under a second.

# The commands whose duration the device or the app sets, not simctl. `bootstatus` waits out a full
# boot and `boot` / `erase` drive the same machinery, while `install` transfers a whole app bundle,
# so its cost scales with the app under test — an input no bound can see. Sized against the roughly
# 80 seconds the iOS end-to-end workflow prices a CI Simulator boot at, since CI is both the slower
# environment and the one where a hang matters; the headroom over that is deliberate, because the
# bound exists to catch a call that will never return, not to police a slow one.
_DEVICE_BLOCKING_TIMEOUT_S = 300.0
_DEVICE_BLOCKING_SUBCOMMANDS = frozenset({"bootstatus", "boot", "erase", "install"})

# Every other command costs only simctl's own small, bounded work, so nothing about the app or the
# scenario can stretch it — `list` returns in well under a second. This sits far above all of them,
# and still catches a wedge long before a CI job's own `timeout-minutes` would.
#
# The pasteboard is the one family the host itself can stall (see `_PBCOPY_*` above), and the two
# halves are bounded differently on purpose. The write runs outside this helper with its own
# per-attempt deadline and a retry, because it was measured stalling transiently and re-feeding the
# same stdin is safe. The read (`pbpaste`) takes this bound and raises, because its result is the
# scenario's data: retrying it is the device-level decision BE-0363 deferred to the recovery ladder,
# and a read that raises at a named deadline already improves on the unbounded hang it replaced.
_SIMCTL_TIMEOUT_S = 60.0


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
    per-module `checked_serial`) precisely
    because that argv-building is spread across modules. The check is the shared `device_id` policy — chiefly that an id never leads with `-`,
    which simctl would read as an option (argv option injection from an untrusted `--udid` / config).

    Raises:
        DeviceError: if `udid` violates the policy — so a bad `--udid` surfaces as the CLI's clean
            exit-2 device fault, the same boundary adb's `checked_serial` uses.
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


def privacy_cmd(udid: str, action: str, tcc_service: str, bundle_id: str) -> list[str]:
    """`simctl privacy <udid> <grant|revoke|reset> <tcc-service> <bundle>` (BE-0276)."""
    return ["xcrun", "simctl", "privacy", validated_udid(udid), action, tcc_service, bundle_id]


def push_cmd(udid: str, bundle_id: str, payload_path: str) -> list[str]:
    return ["xcrun", "simctl", "push", validated_udid(udid), bundle_id, payload_path]


def addmedia_cmd(udid: str, media_path: str) -> list[str]:
    """`simctl addmedia <udid> <path>` — add one photo/video to the device's photo library.

    Unlike `privacy` / `push`, this is device-scoped, not bundle-scoped, and adds a library entry
    on every call rather than being idempotent — a caller that seeds the same path twice gets two
    entries. `seed_photos` (`Preconditions`) calls this once per path for exactly that reason: the
    relative order `simctl` assigns to several assets handed to one invocation is unspecified, so
    only a sequence of single-path calls has a measured, reproducible ordering (roadmap item).
    """
    return ["xcrun", "simctl", "addmedia", validated_udid(udid), media_path]


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


def create_cmd(name: str, device_type: str, runtime: str | None = None) -> list[str]:
    """Create a device of `device_type`, pinned to `runtime` when given.

    `runtime=None` lets simctl pair the newest compatible runtime instead — the fallback
    `create_device` retries with when a pinned create fails, since pinning a runtime the host has
    since dropped is what would make a replacement fail on the very host degradation it exists to
    recover from.
    """
    cmd = [
        "xcrun",
        "simctl",
        "create",
        validated_device_arg(name),
        validated_device_arg(device_type),
    ]
    if runtime is not None:
        cmd.append(validated_device_arg(runtime))
    return cmd


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


def _subcommand_of(args: list[str]) -> str:
    """The token `args` names after `simctl`, or "" when it names none.

    Read structurally rather than by position because `RunFn` is public and `real_run` is a
    default nine other modules import, so an argv with a different prefix must not silently take
    the wrong bound.
    """
    try:
        index = args.index("simctl")
    except ValueError:
        return ""
    return args[index + 1] if index + 1 < len(args) else ""


def _timeout_for(args: list[str]) -> float:
    """The deadline `args` runs under, read off the simctl subcommand it names.

    Classifying here rather than taking the bound from the caller is what lets a new call site
    inherit the right one without its author having to know a bound exists at all.
    """
    if _subcommand_of(args) in _DEVICE_BLOCKING_SUBCOMMANDS:
        return _DEVICE_BLOCKING_TIMEOUT_S
    return _SIMCTL_TIMEOUT_S


def real_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
    full_env = {**os.environ, **(extra_env or {})}
    timeout = _timeout_for(args)
    try:
        return subprocess.run(
            args, capture_output=True, text=True, check=True, env=full_env, timeout=timeout
        ).stdout
    except subprocess.TimeoutExpired as exc:
        raise DeviceTimeout(
            f"device operation timed out after {timeout:g}s: {' '.join(args)}"
            " (this host's CoreSimulator may be wedged)"
        ) from exc


def _probe_timed_out(exc: DeviceTimeout, fallback: str) -> None:
    """Log a best-effort probe's timeout, which the probe folds into `fallback` rather than raising.

    Folding it keeps a diagnostic read from becoming a run-visible fault: BE-0344's recovery ladder
    decides on what a probe observed, and a probe that raised would take that decision away from it.
    Logging it is what keeps the wedge from passing silently, which would diagnose no better than
    the hang this replaced.
    """
    _logger.warning("%s; reporting %s instead", exc, fallback)


def resolve_udid(udid: str, run: RunFn = real_run) -> str:
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
    except DeviceTimeout as exc:
        _probe_timed_out(exc, "the unresolved handle")
        return udid
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return udid
    for devices in (data.get("devices") or {}).values():
        for dev in devices:
            if dev.get("state") == "Booted" and dev.get("udid"):
                return str(dev["udid"])
    return udid


def booted_udids(run: RunFn = real_run) -> list[str]:
    """UDIDs of the currently-booted Simulators (empty on any failure)."""
    try:
        data = json.loads(run(list_booted_cmd(), None))
    except DeviceTimeout as exc:
        _probe_timed_out(exc, "no booted devices")
        return []
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return []
    return [
        str(dev["udid"])
        for devices in (data.get("devices") or {}).values()
        for dev in devices
        if dev.get("state") == "Booted" and dev.get("udid")
    ]


def device_booted(udid: str, run: RunFn = real_run) -> bool | None:
    """Whether `udid` is currently booted, or None when the listing itself failed.

    Three-valued for the same reason `device_available` is: a CoreSimulator wedged enough that
    `simctl shutdown` silently no-ops is also a host where `simctl list devices booted` is likely to
    fail, and `booted_udids`' empty-on-any-failure result would read that as "not booted" — exactly
    the wrong answer for a caller trying to confirm a shutdown actually took.
    """
    try:
        data = json.loads(run(list_booted_cmd(), None))
    except DeviceTimeout as exc:
        _probe_timed_out(exc, "an unknown boot state")
        return None
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError):
        return None
    return any(
        dev.get("udid") == udid and dev.get("state") == "Booted"
        for devices in (data.get("devices") or {}).values()
        for dev in devices
    )


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


def device_catalog(run: RunFn = real_run) -> dict[str, dict[str, str]]:
    """Map udid -> {'name', 'runtime'} for the available simulators (best-effort, {} on any
    failure). Lets a run label which simulator (device model + OS) each scenario ran on."""
    try:
        data = json.loads(run(list_devices_cmd(), None))
    except DeviceTimeout as exc:
        _probe_timed_out(exc, "an empty device catalog")
        return {}
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


def device_available(udid: str, run: RunFn = real_run) -> bool | None:
    """Whether simctl still lists `udid` as available, or None when the probe itself failed.

    The three-valued result is the point: device recovery creates a replacement only on a definite
    `False`. A wedged CoreSimulator makes the listing itself fail, and reading that as "the device
    is gone" would create a replacement on a host that cannot boot one.
    """
    try:
        data = json.loads(run(list_devices_cmd(), None))
    except DeviceTimeout as exc:
        _probe_timed_out(exc, "an unknown availability")
        return None
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError):
        return None
    return any(
        dev.get("udid") == udid
        for devices in (data.get("devices") or {}).values()
        for dev in devices
    )


def device_type_of(udid: str, run: RunFn = real_run) -> tuple[str, str] | None:
    """The device's (`deviceTypeIdentifier`, runtime identifier), so a replacement can clone both.

    None when unresolvable. Reads the unfiltered listing: this is captured while the device is
    healthy, but a device that has become *unavailable* rather than deleted still answers here,
    which keeps the clone possible in the case that matters. The runtime comes from
    `data["devices"]`'s own keys, so it costs no extra simctl call beyond the one already needed
    for the device type.
    """
    try:
        data = json.loads(run(list_all_devices_cmd(), None))
    except DeviceTimeout as exc:
        _probe_timed_out(exc, "an unresolvable device type")
        return None
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError):
        return None
    for runtime, devices in (data.get("devices") or {}).items():
        for dev in devices:
            if dev.get("udid") == udid and dev.get("deviceTypeIdentifier"):
                return str(dev["deviceTypeIdentifier"]), str(runtime)
    return None


def device_type_identifier(name: str, run: RunFn = real_run) -> str | None:
    """The devicetype identifier a human device name resolves to ('iPhone 17 Pro' -> com.apple…).

    None when this host's Xcode ships no such type — the caller then falls back rather than failing,
    since a config default outliving the Xcode that had it is ordinary.
    """
    try:
        data = json.loads(run(list_devicetypes_cmd(), None))
    except DeviceTimeout as exc:
        _probe_timed_out(exc, "no such device type on this host")
        return None
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError):
        return None
    for dev_type in data.get("devicetypes") or []:
        if dev_type.get("name") == name and dev_type.get("identifier"):
            return str(dev_type["identifier"])
    return None


def newest_iphone_device_type(run: RunFn = real_run) -> str | None:
    """The last iPhone devicetype simctl lists (None when it lists none).

    simctl orders devicetypes oldest to newest, so the last iPhone is the newest one installed —
    the same "whichever iPhone this host actually has" choice the CI boot action makes when no
    model is pinned.
    """
    try:
        data = json.loads(run(list_devicetypes_cmd(), None))
    except DeviceTimeout as exc:
        _probe_timed_out(exc, "no iPhone device type")
        return None
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, ValueError):
        return None
    iphones = [
        str(d["identifier"])
        for d in data.get("devicetypes") or []
        if d.get("productFamily") == "iPhone" and d.get("identifier")
    ]
    return iphones[-1] if iphones else None


def create_device(
    device_type: str,
    run: RunFn = real_run,
    *,
    name: str = "bajutsu-recovered",
    runtime: str | None = None,
) -> str:
    """Create a Simulator of `device_type` and return its udid.

    `runtime`, when given, pins the replacement to the vanished device's own iOS version instead of
    whichever one simctl would pick. If the pinned create fails, retries once unpinned — the named
    runtime may be exactly what the host degradation dropped, and any compatible runtime beats
    failing the run outright over one that no longer exists. A caller that only logs the *requested*
    runtime alongside the replacement can read as a claim about what it actually got, so the fallback
    logs its own warning here, next to the decision that made it.

    Raises:
        DeviceError: if simctl could not create the device even unpinned — chiefly a host whose iOS
            runtimes have gone with the device we are replacing, where no replacement is possible at
            all.
    """
    try:
        out = run(create_cmd(name, device_type, runtime), None)
    except subprocess.CalledProcessError as exc:
        if runtime is None:
            raise device_error(exc) from exc
        try:
            out = run(create_cmd(name, device_type), None)
        except subprocess.CalledProcessError as exc2:
            raise device_error(exc2) from exc2
        except OSError as exc2:
            raise DeviceError(f"could not create a replacement Simulator: {exc2}") from exc2
        _logger.warning(
            "could not create %s pinned to runtime %s; created it unpinned instead",
            device_type,
            runtime,
        )
    except OSError as exc:
        raise DeviceError(f"could not create a replacement Simulator: {exc}") from exc
    udid = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if not udid:
        raise DeviceError(f"simctl create {device_type} printed no udid")
    return validated_udid(udid)
