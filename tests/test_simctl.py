"""Tests for the simctl command layer (builders + injectable runner)."""

from __future__ import annotations

import plistlib
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from bajutsu import simctl

# A real `xcrun simctl list devices available -j` payload, captured once from a macOS host and
# committed so the parsers are checked against the shape Xcode actually emits — not only against the
# hand-typed literals above, which are internally consistent and so blind to schema drift (BE-0304).
# Only the home-directory username in the `dataPath` / `logPath` values was normalized to `runner`;
# the parsers read none of those, so the structure the parsers touch (`devices`, per-device `udid` /
# `name` / `state`, the runtime keys) is exactly as captured.
_REAL_LIST_DEVICES = Path(__file__).parent / "data" / "simctl_list_devices_available.json"


def test_command_builders() -> None:
    assert simctl.erase_cmd("U") == ["xcrun", "simctl", "erase", "U"]
    assert simctl.boot_cmd("U") == ["xcrun", "simctl", "boot", "U"]
    assert simctl.openurl_cmd("U", "app://x") == ["xcrun", "simctl", "openurl", "U", "app://x"]
    assert simctl.screenshot_cmd("U", "/p.png") == [
        "xcrun",
        "simctl",
        "io",
        "U",
        "screenshot",
        "/p.png",
    ]
    assert simctl.launch_cmd("U", "com.x", ["-flag", "1"]) == [
        "xcrun",
        "simctl",
        "launch",
        "--terminate-running-process",
        "U",
        "com.x",
        "-flag",
        "1",
    ]
    assert simctl.list_devices_cmd() == ["xcrun", "simctl", "list", "devices", "available", "-j"]
    assert simctl.bootstatus_cmd("U") == ["xcrun", "simctl", "bootstatus", "U", "-b"]
    assert simctl.install_cmd("U", "/p.app") == ["xcrun", "simctl", "install", "U", "/p.app"]
    assert simctl.uninstall_cmd("U", "com.x") == ["xcrun", "simctl", "uninstall", "U", "com.x"]
    assert simctl.get_app_container_cmd("U", "com.x") == [
        "xcrun",
        "simctl",
        "get_app_container",
        "U",
        "com.x",
        "app",
    ]


def test_uninstall_is_idempotent() -> None:
    """uninstall() of an app that isn't installed is a no-op, not a crash."""

    def absent(args: list[str], e: object = None) -> str:
        raise subprocess.CalledProcessError(2, args, stderr="not installed")

    simctl.Env("U", run=absent).uninstall("com.x")  # swallows the error


def test_is_installed_reflects_get_app_container() -> None:
    import subprocess

    def present(args: list[str], e: object = None) -> str:
        return "/path/to.app"

    def absent(args: list[str], e: object = None) -> str:
        raise subprocess.CalledProcessError(2, args, stderr="No such file or directory")

    assert simctl.Env("U", run=present).is_installed("com.x") is True
    assert simctl.Env("U", run=absent).is_installed("com.x") is False  # missing -> False, no raise


def test_booted_udids_parses_simctl() -> None:
    import json

    payload = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                    {"udid": "AAA", "state": "Booted"},
                    {"udid": "BBB", "state": "Shutdown"},
                ],
            }
        }
    )
    assert simctl.booted_udids(run=lambda args, e=None: payload) == ["AAA"]

    def boom(args: list[str], e: object = None) -> str:
        raise OSError("simctl not found")

    assert simctl.booted_udids(run=boom) == []  # failure -> empty, never raises


def test_runtime_label_humanizes_identifier() -> None:
    assert simctl.runtime_label("com.apple.CoreSimulator.SimRuntime.iOS-26-5") == "iOS 26.5"
    assert simctl.runtime_label("com.apple.CoreSimulator.SimRuntime.watchOS-11-0") == "watchOS 11.0"


def test_device_catalog_maps_udid_to_model_and_os() -> None:
    import json

    payload = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-17-2": [
                    {"udid": "AAA", "name": "iPhone 15", "isAvailable": True},
                    {"name": "no-udid-skipped"},
                ],
            }
        }
    )
    catalog = simctl.device_catalog(run=lambda args, e=None: payload)
    assert catalog == {"AAA": {"name": "iPhone 15", "runtime": "iOS 17.2"}}

    def boom(args: list[str], e: object = None) -> str:
        raise OSError("simctl not found")

    assert simctl.device_catalog(run=boom) == {}  # failure -> empty, never raises


def test_device_recovery_command_builders() -> None:
    assert simctl.list_all_devices_cmd() == ["xcrun", "simctl", "list", "devices", "-j"]
    assert simctl.list_devicetypes_cmd() == ["xcrun", "simctl", "list", "devicetypes", "-j"]
    assert simctl.create_cmd("bajutsu-recovered", "com.apple.x.iPhone-17") == [
        "xcrun",
        "simctl",
        "create",
        "bajutsu-recovered",
        "com.apple.x.iPhone-17",
    ]


@pytest.mark.parametrize("bad", ["", "-rf"])
def test_create_cmd_rejects_an_option_shaped_argument(bad: str) -> None:
    with pytest.raises(simctl.DeviceError):
        simctl.create_cmd(bad, "com.apple.x.iPhone-17")
    with pytest.raises(simctl.DeviceError):
        simctl.create_cmd("name", bad)


def test_device_available_is_three_valued() -> None:
    import json

    payload = json.dumps(
        {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-26-0": [{"udid": "AAA"}]}}
    )
    assert simctl.device_available("AAA", run=lambda args, e=None: payload) is True
    assert simctl.device_available("BBB", run=lambda args, e=None: payload) is False

    def boom(args: list[str], e: object = None) -> str:
        raise OSError("simctl not found")

    # A probe that could not run reads as unknown, never as "the device is gone": creating a
    # replacement on a host too sick to list its devices would replace a device needlessly.
    assert simctl.device_available("AAA", run=boom) is None


def test_device_type_of_reads_the_unfiltered_listing() -> None:
    import json

    payload = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                    {"udid": "AAA", "deviceTypeIdentifier": "com.apple.x.iPhone-17-Pro"},
                    {"udid": "BBB"},
                ]
            }
        }
    )
    calls: list[list[str]] = []

    def run(args: list[str], e: object = None) -> str:
        calls.append(args)
        return payload

    assert simctl.device_type_of("AAA", run=run) == "com.apple.x.iPhone-17-Pro"
    assert calls == [simctl.list_all_devices_cmd()]
    assert simctl.device_type_of("BBB", run=run) is None  # listed, but carries no type
    assert simctl.device_type_of("CCC", run=run) is None


def test_device_type_identifier_and_newest_iphone() -> None:
    import json

    payload = json.dumps(
        {
            "devicetypes": [
                {
                    "name": "iPhone 16",
                    "identifier": "com.apple.x.iPhone-16",
                    "productFamily": "iPhone",
                },
                {"name": "iPad Pro", "identifier": "com.apple.x.iPad-Pro", "productFamily": "iPad"},
                {
                    "name": "iPhone 17 Pro",
                    "identifier": "com.apple.x.iPhone-17-Pro",
                    "productFamily": "iPhone",
                },
            ]
        }
    )
    run = lambda args, e=None: payload  # noqa: E731
    assert simctl.device_type_identifier("iPhone 17 Pro", run=run) == "com.apple.x.iPhone-17-Pro"
    assert simctl.device_type_identifier("iPhone 99", run=run) is None
    # simctl lists devicetypes oldest first, so the last iPhone is the newest installed one.
    assert simctl.newest_iphone_device_type(run=run) == "com.apple.x.iPhone-17-Pro"

    def boom(args: list[str], e: object = None) -> str:
        raise OSError("simctl not found")

    assert simctl.device_type_identifier("iPhone 17 Pro", run=boom) is None
    assert simctl.newest_iphone_device_type(run=boom) is None


def test_create_device_returns_the_new_udid() -> None:
    calls: list[list[str]] = []

    def run(args: list[str], e: object = None) -> str:
        calls.append(args)
        return "AAAA-BBBB\n"

    assert simctl.create_device("com.apple.x.iPhone-17", run=run) == "AAAA-BBBB"
    assert calls == [simctl.create_cmd("bajutsu-recovered", "com.apple.x.iPhone-17")]


def test_create_device_fails_loudly_when_no_runtime_remains() -> None:
    def boom(args: list[str], e: object = None) -> str:
        raise subprocess.CalledProcessError(
            1, args, stderr="Invalid runtime: no runtimes are installed"
        )

    with pytest.raises(simctl.DeviceError, match="no runtimes are installed"):
        simctl.create_device("com.apple.x.iPhone-17", run=boom)

    # A device type simctl accepted but printed nothing for leaves no udid to return.
    with pytest.raises(simctl.DeviceError, match="printed no udid"):
        simctl.create_device("com.apple.x.iPhone-17", run=lambda args, e=None: "\n")


def test_locale_args() -> None:
    assert simctl.locale_args("ja_JP") == ["-AppleLocale", "ja_JP", "-AppleLanguages", "(ja)"]
    assert simctl.locale_args("en") == ["-AppleLocale", "en", "-AppleLanguages", "(en)"]


def test_child_env_prefix() -> None:
    assert simctl.child_env({"FOO": "1"}) == {"SIMCTL_CHILD_FOO": "1"}


def test_env_uses_injected_runner() -> None:
    calls: list[tuple[list[str], Mapping[str, str] | None]] = []

    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        calls.append((args, extra_env))
        return ""

    e = simctl.Env("UDID", run=fake_run)
    e.erase()
    e.launch("com.x", ["-a"], {"K": "v"})
    e.openurl("app://settings")

    assert calls[0] == (["xcrun", "simctl", "erase", "UDID"], None)
    assert calls[1][0] == [
        "xcrun",
        "simctl",
        "launch",
        "--terminate-running-process",
        "UDID",
        "com.x",
        "-a",
    ]
    assert calls[1][1] == {"SIMCTL_CHILD_K": "v"}
    assert calls[2] == (["xcrun", "simctl", "openurl", "UDID", "app://settings"], None)


def test_shutdown_is_idempotent() -> None:
    """shutdown() of an already-shut-down device is a no-op, not a crash."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        calls.append(args)
        raise subprocess.CalledProcessError(
            1, args, stderr="Unable to shutdown device in current state: Shutdown"
        )

    simctl.Env("UDID", run=fake_run).shutdown()  # swallows the error
    assert calls == [["xcrun", "simctl", "shutdown", "UDID"]]


def test_command_builders_reject_unvalidated_udid() -> None:
    # Each builder validates the udid inline, so a direct builder call (bypassing Env, as
    # serve does with bootstatus_cmd) can't smuggle an option-injecting / metacharacter id into
    # xcrun argv — the same guarantee every argv builder gives.
    for builder in (simctl.erase_cmd, simctl.boot_cmd, simctl.bootstatus_cmd, simctl.pbpaste_cmd):
        with pytest.raises(simctl.DeviceError, match="invalid udid"):
            builder("-rf; rm")
    with pytest.raises(simctl.DeviceError, match="invalid udid"):
        simctl.launch_cmd("--set", "com.x")


def test_env_validates_udid_at_construction() -> None:
    # Env validates once in __init__ against the shared device-id policy, so every self.udid argv
    # builder (erase/boot/launch/…) is covered — a malicious --udid can never reach a subprocess
    # argv, not just the hand-patched ones. A leading `-` (option injection) / shell metacharacter /
    # space / over-length id is rejected as a DeviceError, so the CLI exits 2 cleanly.
    for bad in ["-rf", "--set", "a b", "a;b", "a$b", "", "x" * 129]:
        with pytest.raises(simctl.DeviceError, match="invalid udid"):
            simctl.Env(bad)
    # UUID- / device-shaped ids and the `booted` alias pass through unchanged.
    for good in ["booted", "U", "A1B2C3D4-1122-3344-5566-77889900AABB"]:
        assert simctl.Env(good).udid == good


def test_parsers_accept_a_real_captured_payload() -> None:
    """`device_catalog` / `booted_udids` parse a real captured payload, not just hand-typed JSON.

    The value is schema fidelity: a future Xcode that renamed `state` or restructured `devices`
    would slip past the hand-typed literals (they encode today's schema by construction) but break
    against this captured one — the gap BE-0304 closes. Injecting the payload through the parsers'
    `run` seam keeps the test hermetic (no `xcrun` on the Linux gate).
    """
    payload = _REAL_LIST_DEVICES.read_text(encoding="utf-8")

    catalog = simctl.device_catalog(run=lambda args, e=None: payload)
    assert catalog, "the captured payload should yield a non-empty device catalog"
    for udid, entry in catalog.items():
        assert udid  # every catalogued device is keyed by a real udid
        assert entry["name"], f"{udid} has no device name"
        # `runtime_label` humanizes the runtime id to e.g. "iOS 26.5"; a schema change to the
        # runtime key would surface here as a raw identifier instead of the "<OS> <ver>" shape.
        assert entry["runtime"].startswith("iOS "), entry["runtime"]

    # The same payload carries per-device `state`, so the booted filter runs against the real shape;
    # the captured host had booted simulators, so the parser must find them (and each must be in the
    # catalog above — booted devices are a subset of the available ones).
    booted = simctl.booted_udids(run=lambda args, e=None: payload)
    assert booted, "the captured payload had booted simulators; the parser should find them"
    assert set(booted) <= set(catalog)


def test_device_error_keeps_command_and_simctl_stderr() -> None:
    exc = subprocess.CalledProcessError(
        149,
        ["xcrun", "simctl", "erase", "U"],
        output="",
        stderr="Unable to erase contents and settings in current state: Booted\n",
    )
    err = simctl.device_error(exc)
    assert isinstance(err, simctl.DeviceError)
    msg = str(err)
    assert "exit 149" in msg
    assert "xcrun simctl erase U" in msg
    assert "Booted" in msg  # simctl's own (actionable) stderr is preserved


def test_run_pbcopy_passes_stdin_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, **kw})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(simctl.subprocess, "run", fake_run)
    simctl.Env("UDID").set_clipboard("hello")

    assert len(calls) == 1  # first attempt succeeds, no retry
    assert calls[0]["cmd"] == ["xcrun", "simctl", "pbcopy", "UDID"]
    assert calls[0]["input"] == "hello"
    assert calls[0]["timeout"] == simctl._PBCOPY_TIMEOUT_S
    assert calls[0]["check"] is True


def test_run_pbcopy_retries_transient_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # simctl exits 60 (ETIMEDOUT) on a flaky pasteboard sync; a re-run clears it.
    attempts = {"n": 0}

    def flaky_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise subprocess.CalledProcessError(60, cmd, output="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    slept: list[float] = []
    monkeypatch.setattr(simctl.subprocess, "run", flaky_run)
    monkeypatch.setattr(simctl.time, "sleep", lambda s: slept.append(s))

    simctl.Env("UDID").set_clipboard("x")  # succeeds on the third attempt, no raise

    assert attempts["n"] == 3
    assert len(slept) == 2  # slept between the two retries only


def test_run_pbcopy_recovers_past_three_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    # A wedged pasteboard can outlast three quick tries (the CI flake this budget was widened
    # for); the retry budget must reach far enough to clear it.
    assert simctl._PBCOPY_MAX_ATTEMPTS >= 4
    attempts = {"n": 0}

    def flaky_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        attempts["n"] += 1
        if attempts["n"] < 4:
            raise subprocess.CalledProcessError(60, cmd, output="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(simctl.subprocess, "run", flaky_run)
    monkeypatch.setattr(simctl.time, "sleep", lambda s: None)

    simctl.Env("UDID").set_clipboard("x")  # succeeds on the fourth attempt, no raise

    assert attempts["n"] == 4


def test_run_pbcopy_reraises_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def always_timeout(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        attempts["n"] += 1
        raise subprocess.CalledProcessError(60, cmd, output="", stderr="")

    monkeypatch.setattr(simctl.subprocess, "run", always_timeout)
    monkeypatch.setattr(simctl.time, "sleep", lambda s: None)

    with pytest.raises(subprocess.CalledProcessError):
        simctl.Env("UDID").clear_clipboard()

    assert attempts["n"] == simctl._PBCOPY_MAX_ATTEMPTS  # bounded, fails loudly after the last


def test_run_pbcopy_fast_fails_non_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A deterministic simctl failure (bad UDID, un-booted device) won't clear on a re-run, so it
    # surfaces on the first attempt — no retry, no backoff.
    attempts = {"n": 0}

    def bad_device(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        attempts["n"] += 1
        raise subprocess.CalledProcessError(149, cmd, output="", stderr="Invalid device")

    slept: list[float] = []
    monkeypatch.setattr(simctl.subprocess, "run", bad_device)
    monkeypatch.setattr(simctl.time, "sleep", lambda s: slept.append(s))

    with pytest.raises(subprocess.CalledProcessError):
        simctl.Env("UDID").set_clipboard("x")

    assert attempts["n"] == 1  # fast-failed, not retried
    assert slept == []


def test_run_pbcopy_retries_python_side_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    # A hang (no simctl exit) surfaces as TimeoutExpired from the subprocess bound; retry it too.
    attempts = {"n": 0}

    def hang_then_ok(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise subprocess.TimeoutExpired(cmd, simctl._PBCOPY_TIMEOUT_S)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(simctl.subprocess, "run", hang_then_ok)
    monkeypatch.setattr(simctl.time, "sleep", lambda s: None)

    simctl.Env("UDID").set_clipboard("x")

    assert attempts["n"] == 2


# --- the Simulator's own system language (BE-0320) --- #


def _globals(mapping: dict[str, object]) -> str:
    """A `defaults export -globalDomain -` payload, as the guest's `defaults` renders it."""
    return plistlib.dumps(mapping).decode()


def test_system_locale_command_builders() -> None:
    # `AppleLanguages` is written as a one-element array (the language subtag alone, matching the
    # app's own `-AppleLanguages` launch argument), `AppleLocale` as the full locale string.
    assert simctl.system_locale_cmds("U", "ja_JP") == [
        [
            "xcrun",
            "simctl",
            "spawn",
            "U",
            "defaults",
            "write",
            "-globalDomain",
            "AppleLanguages",
            "-array",
            "ja",
        ],
        [
            "xcrun",
            "simctl",
            "spawn",
            "U",
            "defaults",
            "write",
            "-globalDomain",
            "AppleLocale",
            "-string",
            "ja_JP",
        ],
    ]
    assert simctl.export_globals_cmd("U") == [
        "xcrun",
        "simctl",
        "spawn",
        "U",
        "defaults",
        "export",
        "-globalDomain",
        "-",
    ]


def test_system_locale_builders_reject_an_option_injecting_locale() -> None:
    # A locale is config-supplied, so it reaches an argv the same way a --udid does: a leading `-`
    # would be read by `defaults` as an option. Rejected before any subprocess sees it.
    for bad in ["-array", "--globalDomain", "ja JP", "ja;rm", ""]:
        with pytest.raises(simctl.DeviceError, match="invalid locale"):
            simctl.system_locale_cmds("U", bad)


def test_language_of_matches_the_app_launch_argument() -> None:
    # The Simulator's system language and the app's own `-AppleLanguages` must never name different
    # languages — that disagreement is exactly what BE-0320 removes.
    for locale in ("en_US", "ja_JP", "fr", "zh_Hans_CN"):
        language = simctl.language_of(locale)
        assert simctl.locale_args(locale)[3] == f"({language})"


def test_pin_system_locale_skips_the_write_when_the_device_already_matches() -> None:
    # The already-pinned case costs one read and no write, so the caller pays no extra boot cycle.
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        calls.append(args)
        return _globals({"AppleLanguages": ["ja"], "AppleLocale": "ja_JP"})

    assert simctl.Env("UDID", run=fake_run).pin_system_locale("ja_JP") is False
    assert calls == [simctl.export_globals_cmd("UDID")]


def test_pin_system_locale_writes_when_the_device_carries_another_language() -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        calls.append(args)
        return _globals({"AppleLanguages": ["en", "ja"], "AppleLocale": "en_JP"})

    assert simctl.Env("UDID", run=fake_run).pin_system_locale("ja_JP") is True
    assert calls[1:] == simctl.system_locale_cmds("UDID", "ja_JP")


def test_a_region_tagged_language_needs_no_rewrite() -> None:
    # The shape a freshly created Simulator inherits from its host: `en-US`, not the bare `en` the
    # pin writes. It already selects the same language, so an exact string comparison would rewrite
    # and reboot every device that was already right — a boot cycle added to every cold spawn.
    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        return _globals({"AppleLanguages": ["en-US"], "AppleLocale": "en_US"})

    e = simctl.Env("UDID", run=fake_run)
    assert e.system_locale_matches("en_US") is True
    assert e.pin_system_locale("en_US") is False


def test_a_fallback_language_behind_the_pinned_one_is_a_mismatch() -> None:
    # The write is a one-element array on purpose: a language queued behind the pinned one lets
    # SpringBoard fall back to it for any string the pinned language lacks, which is the "matched by
    # accident" behaviour BE-0320 removes. A right *primary* language is therefore not a match.
    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        return _globals({"AppleLanguages": ["ja", "en"], "AppleLocale": "ja_JP"})

    e = simctl.Env("UDID", run=fake_run)
    assert e.system_locale_matches("ja_JP") is False
    assert e.pin_system_locale("ja_JP") is True


def test_a_matching_language_with_another_region_is_a_mismatch() -> None:
    # `AppleLocale` decides date / number rendering even when the language agrees, so it is compared
    # too — dropping it from the comparison must not go unnoticed.
    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        return _globals({"AppleLanguages": ["en"], "AppleLocale": "en_GB"})

    assert simctl.Env("UDID", run=fake_run).system_locale_matches("en_US") is False


def test_a_domain_with_no_pinned_language_is_a_mismatch_not_an_unknown() -> None:
    # Readable but carrying no language is positive evidence that nothing pinned it — a mismatch.
    # Classifying it as unknown would let the post-reboot verification pass on a write that never
    # survived the shutdown.
    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        return _globals({"AppleLocale": "ja_JP"})

    assert simctl.Env("UDID", run=fake_run).system_locale_matches("ja_JP") is False


def test_pin_system_locale_rejects_an_option_injecting_locale_before_touching_the_device() -> None:
    # The validation callers actually reach: a malformed locale raises before any subprocess runs,
    # rather than after a wasted round trip (or only when the language happens to differ).
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        calls.append(args)
        return ""

    with pytest.raises(simctl.DeviceError, match="invalid locale"):
        simctl.Env("UDID", run=fake_run).pin_system_locale("-array")
    assert calls == []


def test_system_locale_matches_reports_an_unreadable_domain_as_unknown() -> None:
    # A domain that cannot be read or parsed is neither a match nor a mismatch: the caller writes
    # (it cannot confirm) but never *fails* on it, since nothing was observed to be wrong.
    def unparseable(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        return "not a plist"

    def failing(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        raise subprocess.CalledProcessError(1, args, stderr="device not booted")

    for run in (unparseable, failing):
        assert simctl.Env("UDID", run=run).system_locale_matches("ja_JP") is None
    # A payload that parses but is not a dict is unreadable too (a mangled export), unlike a
    # readable domain that merely lacks the key — that one is a mismatch, covered above.
    mangled = plistlib.dumps([]).decode()
    assert simctl.Env("UDID", run=lambda a, e=None: mangled).system_locale_matches("ja") is None


def test_device_type_label_recovers_the_model_name() -> None:
    assert (
        simctl.device_type_label("com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro")
        == "iPhone 17 Pro"
    )
    assert (
        simctl.device_type_label("com.apple.CoreSimulator.SimDeviceType.iPad-Pro-11-inch-M4")
        == "iPad Pro 11 inch M4"
    )
    # The family token is what `serve`'s capability inventory reads by substring, so it must survive.
    assert "iphone" in simctl.device_type_label("com.apple.x.iPhone-16").lower()
