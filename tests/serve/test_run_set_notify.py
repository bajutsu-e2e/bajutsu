"""Tests for a `run-set` request's `notify` selections (BE-0500 unit 2).

A request names operator-defined sinks, never a URL. A `github-check` selection names the commit to
check, and the repository comes from the caller's machine session alone, so a workflow writes checks
to its own repository and nowhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _shared import queue_repository
from conftest import FakeObjectStore

from bajutsu.serve.group_notify import GroupSink
from bajutsu.serve.operations.dispatch import start_run_set
from bajutsu.serve.sessions.principal import machine_identity
from bajutsu.serve.state import ServeState

_SHA = "0123456789abcdef0123456789abcdef01234567"
_MACHINE = machine_identity("Acme/App")


def _state(tmp_path: Path) -> ServeState:
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "one.yaml").write_text("- name: a\n  steps:\n    - tap: { id: x }\n", "utf-8")
    (tmp_path / "app.apk").write_text("APK", encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n"
        "  demo:\n"
        "    platform: android\n"
        "    subject: {id: com.example.demo, path: app.apk}\n"
        "    paths: {scenarios: scenarios}\n",
        encoding="utf-8",
    )
    state = ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=tmp_path / "runs",
        cwd=tmp_path,
        repository=queue_repository(),
        group_sinks={"github": GroupSink(name="github", format="github-check")},
    )
    state.object_store = FakeObjectStore()
    return state


def _run_set(
    state: ServeState, notify: object, *, actor: str | None = _MACHINE, **body: Any
) -> tuple[Any, int]:
    return start_run_set(
        state, {"target": "demo", "notify": notify, **body}, actor=actor, machine_org="default"
    )


def test_a_selection_is_stored_with_the_target_and_the_sessions_repository(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    assert state.repository is not None

    payload, status = _run_set(state, [{"sink": "github", "headSha": _SHA}])

    assert status == 200, payload
    group = state.repository.get_job_group(payload["groupId"])
    assert group is not None
    assert group["notify"] == {
        "repository": "acme/app",
        "target": "demo",
        "sinks": [{"sink": "github", "headSha": _SHA}],
    }


def test_a_request_without_notify_stores_none(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert state.repository is not None

    payload, status = start_run_set(state, {"target": "demo"})

    assert status == 200
    group = state.repository.get_job_group(payload["groupId"])
    assert group is not None and group["notify"] is None


@pytest.mark.parametrize(
    ("notify", "needle"),
    [
        ({"sink": "github"}, "notify"),
        (["github"], "notify"),
        ([{"sink": "nope", "headSha": _SHA}], "nope"),
        ([{"sink": "github"}], "headSha"),
        ([{"sink": "github", "headSha": _SHA.upper()}], "headSha"),
        ([{"sink": "github", "headSha": _SHA[:-1]}], "headSha"),
        ([{"sink": "github", "headSha": _SHA, "url": "http://x"}], "url"),
        ([{"sink": "github", "headSha": _SHA}] * 2, "github"),
    ],
    ids=[
        "not a list",
        "not a mapping",
        "unknown sink",
        "no head sha",
        "uppercase sha",
        "short sha",
        "extra field",
        "sink named twice",
    ],
)
def test_a_malformed_selection_is_refused(tmp_path: Path, notify: object, needle: str) -> None:
    state = _state(tmp_path)

    payload, status = _run_set(state, notify)

    assert status == 400
    assert needle in payload["error"]
    assert state.jobs == {}


def test_a_github_check_selection_needs_a_machine_session(tmp_path: Path) -> None:
    state = _state(tmp_path)

    payload, status = start_run_set(
        state, {"target": "demo", "notify": [{"sink": "github", "headSha": _SHA}]}
    )

    assert status == 400
    assert "machine session" in payload["error"]


def test_a_repeated_key_with_the_same_selection_returns_its_group(tmp_path: Path) -> None:
    state = _state(tmp_path)
    notify = [{"sink": "github", "headSha": _SHA}]

    first, _ = _run_set(state, notify, groupKey="ci-1")
    again, status = _run_set(state, notify, groupKey="ci-1")

    assert status == 200 and again == first


def test_a_repeated_key_with_another_commit_is_a_conflict(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _run_set(state, [{"sink": "github", "headSha": _SHA}], groupKey="ci-1")

    _, status = _run_set(state, [{"sink": "github", "headSha": "f" * 40}], groupKey="ci-1")

    assert status == 409


def test_a_retry_listing_the_sinks_in_another_order_returns_its_group(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.group_sinks["second"] = GroupSink(name="second", format="github-check")
    github = {"sink": "github", "headSha": _SHA}
    second = {"sink": "second", "headSha": _SHA}

    first, _ = _run_set(state, [github, second], groupKey="ci-1")
    again, status = _run_set(state, [second, github], groupKey="ci-1")

    assert status == 200 and again == first


def test_a_group_reaches_github_as_one_check_from_queued_to_its_conclusion(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime, timedelta

    from bajutsu.serve.group_notify import deliver_once
    from bajutsu.serve.group_notify.github_check import GitHubCheckAdapter
    from bajutsu.serve.group_notify.runtime import snapshot_reader

    state = _state(tmp_path)
    repo = state.repository
    assert repo is not None
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def github(method: str, url: str, token: str, body: dict[str, Any] | None) -> Any:
        path = url.rsplit("/repos/", 1)[1].split("?")[0]
        calls.append((f"{method} {path}", body))
        if method == "GET":
            return {"check_runs": [], "id": 99, "external_id": payload_box[0]}
        return {"id": 99, "status": "queued"}

    payload_box: list[str] = []
    adapter = GitHubCheckAdapter(token_for=lambda _repo: "t", request=github)
    clock = [datetime.now(UTC)]

    def deliver() -> None:
        clock[0] += timedelta(seconds=1)
        moment = clock[0]
        deliver_once(
            repo,
            state.group_sinks,
            {"github-check": adapter},
            snapshot_reader(state),
            clock=lambda: moment,
        )

    payload, _ = _run_set(state, [{"sink": "github", "headSha": _SHA}])
    payload_box.append(payload["groupId"])
    deliver()
    leased = repo.lease_job("w1", state.jobs[payload["jobIds"][0]].capabilities)
    assert leased is not None
    deliver()
    assert repo.complete_job(leased.id, {"exitCode": 2})
    deliver()

    assert [c for c, _ in calls] == [
        f"GET acme/app/commits/{_SHA}/check-runs",
        "POST acme/app/check-runs",
        "GET acme/app/check-runs/99",
        "PATCH acme/app/check-runs/99",
        "GET acme/app/check-runs/99",
        "PATCH acme/app/check-runs/99",
    ]
    completed = calls[-1][1]
    assert completed is not None and completed["conclusion"] == "failure"
    assert "one.yaml" in completed["output"]["summary"]
    assert "usage error" in completed["output"]["summary"]
    assert payload["groupId"]


def test_a_repeated_key_from_another_repository_is_a_conflict(tmp_path: Path) -> None:
    state = _state(tmp_path)
    notify = [{"sink": "github", "headSha": _SHA}]
    _run_set(state, notify, groupKey="ci-1")

    _, status = _run_set(state, notify, groupKey="ci-1", actor=machine_identity("acme/fork"))

    assert status == 409
