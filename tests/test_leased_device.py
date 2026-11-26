"""The `leased` environment and device provider (BE-0511), against a stub lease command."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest
import typer
from _lease_stub import events, lease_command

from bajutsu.common.capability.worker_capability import (
    WorkerCapability,
    WorkerCapabilityError,
    parse_worker_capability,
)
from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.runner import device_provider as dp
from bajutsu.common.runner.device_provider import lease_command as broker
from bajutsu.common.runner.device_provider.tagged_udid import entries_for_platform, tag_addresses
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.server.one_shot import environment_refusal

_CONFIG = """
targets:
  ios: { platform: ios, subject: { id: com.x }, runsOn: { ios: { model: "iPhone 16" } } }
  droid: { platform: android, subject: { id: com.x } }
"""


def _yaml(command: list[str], *, platforms: str = "[ios, android]", extra: str = "") -> str:
    return (
        "version: 1\nenvironment: leased\ndrivers: [xcuitest, adb, playwright, fake]\n"
        f"platforms: {platforms}\n"
        f"lease:\n  command: {json.dumps(command)}\n{extra}"
    )


def _worker(command: list[str], **kwargs: str) -> WorkerCapability:
    return parse_worker_capability(_yaml(command, **kwargs))


def _eff(target: str, worker: WorkerCapability) -> Effective:
    eff = resolve(load_config(_CONFIG), target)
    return replace(eff, device_provider=worker.device_provider_for(eff.platform))


def _borrow(command: list[str], **kwargs: float) -> broker.BorrowedDevice:
    return broker.borrow_device(
        command,
        platform="ios",
        device=None,
        acquire_timeout=kwargs.get("acquire_timeout", 10.0),
        release_timeout=kwargs.get("release_timeout", 5.0),
    )


# --- worker.yaml ---------------------------------------------------------------------------


def test_leased_environment_reads_its_broker_and_runs_one_job() -> None:
    cap = _worker(["b", "--device", "{device}"], extra="  acquireTimeout: 2m\n")
    assert cap.is_local
    assert cap.lease is not None
    assert cap.lease.acquire_timeout == 120
    assert cap.lease.release_timeout == 10
    provider = cap.device_provider_for("ios")
    assert provider is not None
    assert (provider.kind, provider.command) == ("leased", ("b", "--device", "{device}"))
    assert cap.device_provider_for("web") is None
    assert _worker(["b"], platforms="[ios]").device_provider_for("android") is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("version: 1\ndrivers: [fake]\nenvironment: leased\n", "needs a `lease:` block"),
        ("version: 1\ndrivers: [fake]\nplatforms: [ios]\n", "belongs to environment 'leased'"),
        (_yaml([]), "command"),
        (_yaml(["b", "{host}"]), "unknown placeholder"),
        (_yaml(["b", "--device={device}"]), "whole element"),
        (_yaml(["{device}"]), "follow the flag"),
        (_yaml(["b", "{device}"]), "follow the flag"),
        (_yaml(["b", "{platform}", "{device}"]), "follow the flag"),
        (_yaml(["b"], platforms="[web]"), "platforms"),
        (_yaml(["b"], platforms="[]"), "platforms"),
        (_yaml(["b"], extra="  acquireTimeout: soon\n"), "duration"),
        (_yaml(["b"], extra="maxJobConcurrency: 2\n"), "one job at a time"),
    ],
)
def test_leased_environment_refuses_a_bad_file(text: str, reason: str) -> None:
    with pytest.raises(WorkerCapabilityError, match=reason):
        parse_worker_capability(text)


def test_leased_worker_advertises_the_platforms_of_its_file() -> None:
    cap = _worker(["b"], platforms="[android]")
    caps = worker_mod._advertised_capabilities("", "", cap, "linux")
    assert "platform:android" in caps
    assert "platform:ios" not in caps
    assert not [c for c in caps if c.startswith("environment:")]
    with pytest.raises(typer.BadParameter, match="takes no --platform"):
        worker_mod._advertised_capabilities("ios", "", cap, "darwin")
    # Its real devices belong to the broker, and it registers no batch provider.
    assert worker_mod._device_reader(["platform:ios"], _worker(["b"])) is None
    worker_mod._register_environment_or_exit("leased")


def test_leased_worker_routes_only_the_platforms_it_borrows_for() -> None:
    drivable = worker_mod._drivable(_worker(["b"], platforms="[android]"), "darwin")
    assert "xcuitest" not in drivable
    assert {"adb", "playwright"} <= set(drivable)


def test_leased_worker_takes_a_run_spec_and_no_batch() -> None:
    assert environment_refusal({"cmd": []}, "leased") is None
    assert environment_refusal({"batch": {"provider": "devicefarm"}}, "leased") is not None


# --- the lease command ---------------------------------------------------------------------


def test_argv_fills_placeholders_and_drops_an_unnamed_device() -> None:
    command = ["b", "--on={platform}", "--device", "{device}"]
    assert broker.lease_argv(command, "ios", "iPhone 16") == [
        "b",
        "--on=ios",
        "--device",
        "iPhone 16",
    ]
    assert broker.lease_argv(command, "ios", None) == ["b", "--on=ios"]
    with pytest.raises(ValueError, match="begins with '-'"):
        broker.lease_argv(command, "ios", "--all")


def test_borrow_reads_the_answer_and_release_ends_the_command(tmp_path: Path) -> None:
    command, log = lease_command(tmp_path)
    borrowed = _borrow(command)
    assert borrowed.address == "AAAA-1111"
    assert borrowed.env == {"STUB_PORT": "5038"}
    borrowed.release()
    borrowed.release()  # a second release does nothing
    assert events(log)[1:] == ["terminated ios"]


def test_release_forces_a_command_that_ignores_the_signal(tmp_path: Path) -> None:
    command, _ = lease_command(tmp_path, "stubborn")
    borrowed = _borrow(command, release_timeout=0.3)
    borrowed.release()  # returns rather than waiting out the wedged command


def test_a_broker_that_logs_past_its_answer_never_blocks(tmp_path: Path) -> None:
    command, log = lease_command(tmp_path, "chatty")
    borrowed = _borrow(command)
    # 800 KB past the answer fills any pipe buffer, so reaching this line proves it was drained.
    for _ in range(200):
        if "logged past its answer" in events(log):
            break
        threading.Event().wait(0.05)
    borrowed.release()
    assert "logged past its answer" in events(log)


def test_a_command_that_exits_first_declines_with_its_reason(tmp_path: Path) -> None:
    command, _ = lease_command(tmp_path, "decline")
    with pytest.raises(broker.LeaseDeclinedError, match="no device free"):
        _borrow(command)


def test_a_silent_command_declines_after_the_timeout(tmp_path: Path) -> None:
    command, log = lease_command(tmp_path, "silent")
    with pytest.raises(broker.LeaseDeclinedError, match="printed no line"):
        _borrow(command, acquire_timeout=2.0)
    assert "terminated ios" in events(log)


def test_a_missing_command_declines(tmp_path: Path) -> None:
    with pytest.raises(broker.LeaseDeclinedError, match="cannot start"):
        _borrow([str(tmp_path / "absent")])


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (b"nope\n", "not a JSON object"),
        (b"[]\n", "device.address"),
        (b'{"device": {}}\n', "device.address"),
        (b'{"device": {"address": "a,b"}}\n', "device.address"),
        (b'{"device": {"address": "a"}, "environment": {"P": 5}}\n', "string values"),
    ],
)
def test_a_malformed_answer_is_an_error_not_a_decline(answer: bytes, reason: str) -> None:
    with pytest.raises(ValueError, match=reason) as caught:
        broker._parse_answer(answer)
    assert not isinstance(caught.value, broker.LeaseDeclinedError)


# --- the provider and the tagged `--udid` --------------------------------------------------


def test_tagged_entries_are_split_by_platform() -> None:
    udid = tag_addresses([("ios", "AAAA"), ("ios", "BBBB"), ("android", "192.0.2.7:5555")])
    assert entries_for_platform(udid, "ios") == "AAAA,BBBB"
    assert entries_for_platform(udid, "android") == "192.0.2.7:5555"
    # An untagged entry serves every platform, so a hand-written list still works.
    assert entries_for_platform("CCCC", "android") == "CCCC"
    with pytest.raises(ValueError, match="unknown platform 'tv'"):
        entries_for_platform("tv=1", "ios")
    with pytest.raises(ValueError, match="no entry for platform 'ios'"):
        entries_for_platform("android=1", "ios")


def test_provider_borrows_for_a_run_with_no_udid(tmp_path: Path) -> None:
    command, log = lease_command(tmp_path)
    lease = dp.acquire_device(_eff("ios", _worker(command)), "booted")
    assert (lease.udid_spec, lease.env) == ("AAAA-1111", {"STUB_PORT": "5038"})
    lease.release()
    assert events(log) == ["argv --platform ios --device iPhone 16", "terminated ios"]


def test_provider_takes_addresses_a_worker_already_borrowed(tmp_path: Path) -> None:
    command, log = lease_command(tmp_path)
    lease = dp.acquire_device(_eff("droid", _worker(command)), "ios=AAAA,android=emulator-1")
    assert (lease.udid_spec, lease.env) == ("emulator-1", {})
    lease.release()
    assert events(log) == []  # nothing borrowed


def test_conflicting_lease_variables_are_refused() -> None:
    merged: dict[str, str] = {}
    dp.merge_lease_env(merged, {"P": "1"})
    dp.merge_lease_env(merged, {"P": "1", "Q": "2"})
    with pytest.raises(dp.LeaseEnvConflictError, match="values for P"):
        dp.merge_lease_env(merged, {"P": "3"})


def test_a_run_applies_each_lease_env_and_releases_on_a_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.run import cli as run_cli

    command, log = lease_command(tmp_path, "per-device-env")
    worker = _worker(command)
    effs = {name: _eff(name, worker) for name in ("ios", "droid")}
    monkeypatch.setattr(
        run_cli,
        "_select_actuator",
        lambda _backend, eff, _engines, _worker: (eff.platform, [eff.platform]),
    )

    class _Env:
        def resolve_device(self, udid: str) -> str:
            assert os.environ["STUB_PORT"]  # applied before any device is resolved
            return udid

    monkeypatch.setattr(run_cli, "environment_for", lambda _actuator, _udid: _Env())
    monkeypatch.setenv("STUB_PORT", "before")
    with pytest.raises(typer.Exit):
        run_cli._acquire_targets(effs, "", [], "booted", 1, worker)
    assert sorted(e for e in events(log) if e.startswith("terminated")) == [
        "terminated android",
        "terminated ios",
    ]


def test_a_device_command_releases_its_lease_when_it_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import click

    from bajutsu.cli._shared import _device_udid_or_exit

    command, log = lease_command(tmp_path)
    monkeypatch.setenv("STUB_PORT", "before")
    with click.Context(click.Command("record")):
        assert _device_udid_or_exit(_eff("ios", _worker(command)), "booted") == "AAAA-1111"
        assert os.environ["STUB_PORT"] == "5038"
        assert "terminated ios" not in events(log)
    assert "terminated ios" in events(log)


def test_the_mcp_probe_releases_its_lease_and_restores_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.mcp.tools import _probe_device

    command, log = lease_command(tmp_path)
    worker = _worker(command)
    monkeypatch.setattr("bajutsu.mcp.tools.select_actuator", lambda *_a, **_k: "xcuitest")
    monkeypatch.setattr(
        "bajutsu.mcp.tools.first_run_device", lambda eff, _actuator, spec: (eff, spec)
    )
    monkeypatch.delenv("STUB_PORT", raising=False)
    eff = resolve(load_config(_CONFIG), "ios")
    with _probe_device(eff, "booted", worker) as (_, spec):
        assert (spec, os.environ["STUB_PORT"]) == ("AAAA-1111", "5038")
    assert "STUB_PORT" not in os.environ
    assert "terminated ios" in events(log)
