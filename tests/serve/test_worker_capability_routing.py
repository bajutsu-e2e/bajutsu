"""Advertising and routing under the worker capability (BE-0450 unit 5).

A worker advertises `host:<os>` for the machine it runs on, a job whose driver runs on one host
operating system requires it, and a local worker refuses a `--platform` its worker.yaml or its host
rules out. The hand-written token inputs (`--capabilities`, `$BAJUTSU_WORKER_CAPABILITIES`, and
`requires`) still route, with a one-time notice, and none may carry the reserved `environment:`
prefix. Everything here is pure or in-memory SQLite: no Simulator, no network.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import typer
from pydantic import ValidationError
from sqlalchemy import Engine

from bajutsu.common import deprecations
from bajutsu.common.backend_cli import simctl
from bajutsu.common.capability.worker_capability import WorkerCapability
from bajutsu.common.config import load_config, resolve
from bajutsu.serve import capabilities as cap
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.helpers import target_capabilities
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.models import Base


@pytest.fixture(autouse=True)
def _fresh_notices(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # The notices are once per process; start each test from none so its own notice is observable.
    saved = set(deprecations._emitted)
    deprecations._emitted.clear()
    monkeypatch.delenv(cap.WORKER_CAPABILITIES_ENV, raising=False)
    # No Simulator inventory: the advertised set is the flags, the host, and the override alone.
    monkeypatch.setattr(simctl, "real_run", None)
    yield
    deprecations._emitted.clear()
    deprecations._emitted.update(saved)


def _local(*drivers: str) -> WorkerCapability:
    return WorkerCapability(version=1, drivers=drivers or ("xcuitest", "adb", "playwright", "fake"))


def _repo(serve_engine: Callable[..., Engine]) -> SqlRepository:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    return SqlRepository(engine)


def _config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "bajutsu.config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --- The host axis -------------------------------------------------------------------------------


def test_required_host_is_the_one_os_every_actuator_is_bound_to() -> None:
    assert cap.required_host(["xcuitest"]) == "darwin"
    # A fallback that can resolve to a driver running anywhere requires no host.
    assert cap.required_host(["xcuitest", "playwright"]) is None
    assert cap.required_host(["adb"]) is None
    assert cap.required_host([]) is None
    # An actuator with no driver yet is no constraint: the worker could not select it anyway.
    assert cap.required_host(["flutter"]) is None


def test_required_host_needs_a_single_os() -> None:
    def lookup(actuator: str) -> frozenset[str] | None:
        return frozenset({"darwin", "linux"}) if actuator == "a" else frozenset({"linux"})

    assert cap.required_host(["a"], lookup) is None
    assert cap.required_host(["b"], lookup) == "linux"


def test_required_capabilities_adds_the_host_token() -> None:
    assert cap.required_capabilities("ios", host="darwin") == ["host:darwin", "platform:ios"]
    assert cap.required_capabilities("web", host=None) == ["platform:web"]


def test_a_target_requires_the_host_of_the_drivers_run_selects_from(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        "targets:\n"
        "  app: { bundleId: com.example.app }\n"
        "  fallback: { bundleId: com.example.app, backend: [ios, web] }\n"
        "  faked: { bundleId: com.example.app, backend: [fake] }\n"
        "  site: { baseUrl: 'https://example.com', backend: [web] }\n",
    )
    assert target_capabilities(cfg, "app") == ["host:darwin", "platform:ios"]
    # The same actuators `run` selects from: a fallback that may resolve to `playwright`, or the
    # fake backend, runs anywhere, so neither requires macOS.
    assert target_capabilities(cfg, "fallback") == ["platform:ios"]
    assert target_capabilities(cfg, "faked") == ["platform:ios"]
    assert target_capabilities(cfg, "site") == ["platform:web"]


def test_a_local_worker_advertises_its_host() -> None:
    assert worker_mod._advertised_capabilities("web", "", _local(), "linux") == [
        "host:linux",
        "platform:web",
    ]


def test_an_xcuitest_job_routes_only_to_a_darwin_worker(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The lease, the unroutable count, and the no-worker notice all read `can_serve`, so the job's
    # derived host requirement holds in each alike.
    cfg = _config(tmp_path, "targets:\n  app: { bundleId: com.example.app }\n")
    required = target_capabilities(cfg, "app")
    # A Linux worker that still claims `platform:ios` through the deprecated override.
    linux = worker_mod._advertised_capabilities("web", "platform:ios", _local(), "linux")
    mac = worker_mod._advertised_capabilities("ios", "", _local(), "darwin")

    repo = _repo(serve_engine)
    repo.enqueue_job("ios-job", "o", {}, capabilities=required)
    repo.register_worker("linux", linux)
    assert repo.metrics_snapshot().unroutable_queued == 1
    assert repo.lacks_live_worker("ios-job")
    assert repo.lease_job("linux", linux) is None

    repo.register_worker("mac", mac)
    assert repo.metrics_snapshot().unroutable_queued == 0
    assert not repo.lacks_live_worker("ios-job")
    leased = repo.lease_job("mac", mac)
    assert leased is not None and leased.id == "ios-job"


# --- The --platform rules of a local worker ------------------------------------------------------


def test_a_linux_worker_refuses_the_ios_platform() -> None:
    # The default `--platform` is `ios`, so a Linux machine stops with the reason at startup.
    with pytest.raises(typer.BadParameter, match="xcuitest runs only on macOS; this worker runs"):
        worker_mod._advertised_capabilities("", "", _local(), "linux")


def test_a_local_worker_refuses_a_platform_its_file_omits() -> None:
    with pytest.raises(typer.BadParameter, match="adb is not among this worker's drivers"):
        worker_mod._advertised_capabilities("web,android", "", _local("playwright"), "linux")


def test_a_local_worker_serves_the_platforms_its_file_and_host_allow() -> None:
    assert worker_mod._advertised_capabilities(
        "ios,web", "", _local("xcuitest", "playwright"), "darwin"
    ) == ["host:darwin", "platform:ios", "platform:web"]


# --- The deprecated token inputs ------------------------------------------------------------------


def test_the_capabilities_flag_warns_and_is_still_honored(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        caps = worker_mod._advertised_capabilities("web", "ipad", _local(), "linux")
    assert "ipad" in caps
    assert "--capabilities is deprecated (BE-0450)" in caplog.text
    assert "worker.yaml" in caplog.text


def test_the_capabilities_variable_warns_and_is_still_honored(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cap.WORKER_CAPABILITIES_ENV, "ios18")
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        caps = worker_mod._advertised_capabilities("web", "", _local(), "linux")
    assert "ios18" in caps
    assert "$BAJUTSU_WORKER_CAPABILITIES is deprecated (BE-0450)" in caplog.text


def test_no_override_means_no_notice(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        worker_mod._advertised_capabilities("web", "", _local(), "linux")
    assert caplog.text == ""


@pytest.mark.parametrize("where", ["defaults", "target"])
def test_requires_warns_and_is_still_honored(where: str, caplog: pytest.LogCaptureFixture) -> None:
    body = (
        "defaults:\n  requires: [ios18]\ntargets:\n  s: { bundleId: com.x }\n"
        if where == "defaults"
        else "targets:\n  s: { bundleId: com.x, requires: [ios18] }\n"
    )
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        cfg = load_config(body)
    assert resolve(cfg, "s").requires == ["ios18"]
    assert "requires' is deprecated (BE-0450)" in caplog.text
    assert "device" in caplog.text
    assert "config load" in caplog.text


def test_an_empty_requires_says_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="bajutsu.common.deprecations"):
        load_config("targets:\n  s: { bundleId: com.x, requires: [] }\n")
    assert caplog.text == ""


@pytest.mark.parametrize("token", ["environment:devicefarm", "host:darwin"])
@pytest.mark.parametrize("where", ["defaults", "target"])
def test_requires_refuses_the_reserved_prefixes(token: str, where: str) -> None:
    body = (
        f"defaults:\n  requires: [{token}]\ntargets:\n  s: {{ bundleId: com.x }}\n"
        if where == "defaults"
        else f"targets:\n  s: {{ bundleId: com.x, requires: [ios18, '{token}'] }}\n"
    )
    with pytest.raises(ValidationError, match="host: prefixes are reserved"):
        load_config(body)


@pytest.mark.parametrize("token", ["environment:devicefarm", "host:darwin"])
def test_the_override_refuses_the_reserved_prefixes(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The worker fills both itself, so neither the flag nor the variable may claim one.
    with pytest.raises(typer.BadParameter, match="reserved"):
        worker_mod._advertised_capabilities("web", token, _local(), "linux")
    monkeypatch.setenv(cap.WORKER_CAPABILITIES_ENV, f"ios18,{token}")
    with pytest.raises(typer.BadParameter, match="reserved"):
        worker_mod._advertised_capabilities("web", "", _local(), "linux")
