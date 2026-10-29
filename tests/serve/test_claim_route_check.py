"""A worker re-checks a leased job's route tokens before it leases a device (BE-0467)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.capabilities import claim_refusal
from bajutsu.serve.cli import worker as worker_mod
from bajutsu.serve.logbus import InMemoryLogBus
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base

_SIM_QUERY = "cap:ios-simulator:query"


def test_a_route_token_the_worker_still_offers_passes() -> None:
    assert claim_refusal(["platform:ios", _SIM_QUERY], {_SIM_QUERY}) is None


def test_a_route_token_the_worker_lost_refuses_the_job() -> None:
    reason = claim_refusal(["platform:ios", _SIM_QUERY], {"cap:web:query"})
    assert reason is not None and _SIM_QUERY in reason
    # Only route tokens are re-checked; the rest were judged at lease.
    assert "platform:ios" not in reason


def test_a_worker_with_no_route_of_its_own_is_not_rechecked() -> None:
    assert claim_refusal([_SIM_QUERY], None) is None


def test_the_lease_hands_the_worker_its_routing_set(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    state = srv.ServeState(
        runs_dir=tmp_path / "runs", executor=DbQueueExecutor(repo), repository=repo
    )
    repo.enqueue_job("j", "o", {"job_id": "j"}, capabilities=["platform:ios", _SIM_QUERY])
    payload, code = ops.worker_lease(state, "w", ["platform:ios", _SIM_QUERY])
    assert code == 200
    assert payload["capabilities"] == ["platform:ios", _SIM_QUERY]


def _worker_yaml(tmp_path: Path, drivers: str) -> Path:
    path = tmp_path / "worker.yaml"
    path.write_text(f"version: 1\ndrivers: [{drivers}]\n", encoding="utf-8")
    return path


def test_a_worker_whose_file_dropped_the_driver_refuses_its_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bajutsu.common.backends.current_host_os", lambda: "darwin")
    required = ["platform:ios", _SIM_QUERY]
    assert worker_mod._claim_refusal(required, _worker_yaml(tmp_path, "xcuitest")) is None
    # The operator removed xcuitest from worker.yaml after the worker registered.
    reason = worker_mod._claim_refusal(required, _worker_yaml(tmp_path, "playwright"))
    assert reason is not None and _SIM_QUERY in reason


def test_a_lease_from_a_server_before_this_item_is_not_rechecked(tmp_path: Path) -> None:
    assert worker_mod._claim_refusal(None, _worker_yaml(tmp_path, "playwright")) is None


def test_a_refused_claim_fails_before_anything_is_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_mod, "_post_json", lambda *_a, **_k: (200, {}))

    def fetch(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a refused claim must not fetch the bundle")

    monkeypatch.setattr(worker_mod, "_workspace_or_failure", fetch)
    result, abandoned, _work = worker_mod._run_with_heartbeat(
        {"cmd": ["x"]},
        job_id="j1",
        work=tmp_path,
        bundle_urls=None,
        bus=InMemoryLogBus(),
        url="http://cp",
        wid="w1",
        auth_token=None,
        heartbeat_interval=0.02,
        claim_check=lambda: "missing cap:ios-simulator:query",
    )
    assert not abandoned
    assert result == {"ok": False, "error": "missing cap:ios-simulator:query"}


def test_an_unreadable_worker_yaml_refuses_the_job_naming_why(tmp_path: Path) -> None:
    path = tmp_path / "worker.yaml"
    path.write_text("version: 2\n", encoding="utf-8")
    reason = worker_mod._claim_refusal([_SIM_QUERY], path)
    assert reason is not None and "worker.yaml can no longer be read" in reason


def test_an_appium_worker_rechecks_against_the_live_route(tmp_path: Path) -> None:
    path = tmp_path / "worker.yaml"
    path.write_text(
        "version: 1\nenvironment: appium\nappium: {endpoint: 'https://grid.example/wd/hub'}\n"
        "drivers: [xcuitest]\n",
        encoding="utf-8",
    )
    assert worker_mod._claim_refusal(["cap:ios-live:query"], path) is None
    assert worker_mod._claim_refusal([_SIM_QUERY], path) is not None


def test_a_cloud_batch_worker_is_not_rechecked(tmp_path: Path) -> None:
    path = tmp_path / "worker.yaml"
    path.write_text(
        "version: 1\nenvironment: devicefarm\nmaxJobConcurrency: 2\ndrivers: [xcuitest]\n",
        encoding="utf-8",
    )
    assert worker_mod._claim_refusal([_SIM_QUERY], path) is None
