"""Tests for wiring group-event delivery into `serve` (BE-0500 unit 6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from _shared import queue_repository

from bajutsu.serve.group_notify import GroupSink
from bajutsu.serve.group_notify.runtime import snapshot_reader, start_group_notify
from bajutsu.serve.server.db import GroupEventClaim, QueuedJob
from bajutsu.serve.state import ServeState


def test_no_sink_starts_no_loop(tmp_path: Path) -> None:
    state = ServeState(runs_dir=tmp_path / "runs", repository=queue_repository())

    assert start_group_notify(state, {}) is None


def test_no_store_starts_no_loop(tmp_path: Path) -> None:
    state = ServeState(
        runs_dir=tmp_path / "runs",
        group_sinks={"github": GroupSink(name="github", format="github-check")},
    )

    assert start_group_notify(state, {}) is None


def test_the_app_token_reads_the_key_file_and_resolves_the_installation_per_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bajutsu.common.github.app as app
    from bajutsu.serve.group_notify import runtime

    key = tmp_path / "app.pem"
    key.write_text("PEM", encoding="utf-8")
    seen: list[tuple[object, ...]] = []

    def mint(*a: object, **k: object) -> str:
        seen.append((*a, *k.values()))
        return "ghs_x"

    monkeypatch.setattr(app, "repository_installation_token", mint)
    env = {
        "BAJUTSU_GITHUB_APP_ID": "12",
        "BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE": str(key),
        "BAJUTSU_GITHUB_APP_INSTALLATION_ID": "34",
    }

    assert runtime._app_token(env, "acme/app") == "ghs_x"
    assert seen == [("12", "PEM", "acme/app")]


def test_a_state_with_a_sink_and_a_store_starts_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.serve.group_notify import runtime

    started: list[tuple[object, ...]] = []
    thread = object()

    def start(*a: object) -> object:
        started.append(a)
        return thread

    monkeypatch.setattr(runtime, "start_delivery_loop", start)
    state = ServeState(
        runs_dir=tmp_path / "runs",
        repository=queue_repository(),
        group_sinks={"github": GroupSink(name="github", format="github-check")},
    )

    assert start_group_notify(state, {}) is thread
    assert set(started[0][2]) == {"github-check"}  # type: ignore[call-overload]


def test_the_snapshot_reads_each_failed_runs_manifest_from_the_groups_org(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    (runs / "r1").mkdir(parents=True)
    (runs / "r1" / "manifest.json").write_text(
        json.dumps({"scenarios": [{"scenario": "a", "ok": False, "failure": "no Done button"}]}),
        encoding="utf-8",
    )
    state = ServeState(runs_dir=runs, repository=queue_repository())
    assert state.repository is not None
    state.repository.register_job_group(
        "g1",
        org_id="default",
        key="k",
        digest="d",
        compat_key=None,
        jobs=[QueuedJob(id="1", spec={"intent": {"scenario": "one.yaml"}})],
    )
    assert state.repository.lease_job("w1") is not None
    assert state.repository.complete_job("1", {"exitCode": 1, "runId": "r1"})
    claim = GroupEventClaim(
        id="e",
        token="t",
        group_id="g1",
        org_id="default",
        sink="github",
        event="completed",
        occurred_at=datetime.now(UTC),
        attempts=0,
        reference=None,
        snapshot=None,
        notify={},
    )

    snapshot = snapshot_reader(state)(claim)

    assert snapshot["rows"] == [
        {"scenario": "one.yaml", "outcome": "failed", "reason": "no Done button"}
    ]
