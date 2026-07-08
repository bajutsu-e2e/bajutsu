"""Tests for the worker artifact/scenario presigned-URL operations (BE-0160).

`worker_artifact_urls` / `worker_scenario_url` are the control-plane operations a worker calls to
upload a run's artifact tree and a `record` job's authored scenario over plain HTTP — with none of
its own cloud credentials. The server holds the object store, resolves the org from the *leased job*
(never a worker-supplied value) so a worker can't cross into another tenant's prefix, and re-validates
every worker-supplied relative key. These mirror the evidence path (BE-0110) for the artifact and
scenario destinations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _shared import _post, _serve

from bajutsu.serve import operations as ops
from bajutsu.serve.state import ServeState


class _FakeStore:
    """The slice of `ObjectStore` the operations use: a signed PUT/GET URL per key, plus a listing
    for baselines. In-memory, so the gate needs no cloud SDK or network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self.objects[key] = data

    def presigned_put_url(self, key: str, *, content_type: str = "", ttl: int = 3600) -> str:
        return f"https://signed.example/put/{key}?ct={content_type}"

    def presigned_url(self, key: str) -> str:
        return f"https://signed.example/get/{key}"

    def list_keys(self, prefix: str) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))


class _FakeRepo:
    """The DB boundary the operation reads the leased job from: {job_id: org_id}. By default every
    job is ``leased`` by worker ``w1``; *status* / *leased_by* override that to exercise the lease
    guard (a done/failed job, or one another worker holds)."""

    def __init__(
        self, jobs: dict[str, str], *, status: str = "leased", leased_by: str = "w1"
    ) -> None:
        self._jobs = jobs
        self._status = status
        self._leased_by = leased_by

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        org = self._jobs.get(job_id)
        if org is None:
            return None
        return {
            "status": self._status,
            "result": {},
            "org_id": org,
            "leased_by": self._leased_by,
        }


def _state(
    tmp_path: Path, *, store: _FakeStore | None, repo: _FakeRepo | None = None, jobs: dict[str, str]
) -> ServeState:
    state = ServeState(runs_dir=tmp_path / "runs")
    state.object_store = store
    state.object_store_prefix = ""
    state.repository = repo or _FakeRepo(jobs)  # type: ignore[assignment]
    return state


# --- worker_artifact_urls -----------------------------------------------------------------------


def test_no_object_store_returns_no_urls(tmp_path: Path) -> None:
    state = _state(tmp_path, store=None, jobs={"j1": "default"})
    payload, status = ops.worker_artifact_urls(
        state, {"job_id": "j1", "worker_id": "w1", "run_id": "20260704-1", "files": ["report.html"]}
    )
    assert status == 200
    assert payload == {"urls": {}}


def test_artifact_urls_key_under_default_org_artifact_prefix(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    payload, status = ops.worker_artifact_urls(
        state,
        {
            "job_id": "j1",
            "worker_id": "w1",
            "run_id": "20260704-1",
            "files": ["report.html", "sub/shot.png"],
        },
    )
    assert status == 200
    assert payload["urls"] == {
        "report.html": "https://signed.example/put/artifacts/20260704-1/report.html?ct=text/html",
        "sub/shot.png": "https://signed.example/put/artifacts/20260704-1/sub/shot.png?ct=image/png",
    }


def test_artifact_urls_key_under_the_orgs_segment(tmp_path: Path) -> None:
    # A non-default org's artifacts land under its org segment, matching the control plane's
    # org-scoped artifact store (BE-0015 multi-tenancy) — resolved from the job, not the worker.
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "acme"})
    payload, status = ops.worker_artifact_urls(
        state, {"job_id": "j1", "worker_id": "w1", "run_id": "20260704-1", "files": ["report.html"]}
    )
    assert status == 200
    assert payload["urls"]["report.html"].startswith(
        "https://signed.example/put/acme/artifacts/20260704-1/report.html"
    )


def test_artifact_urls_unknown_job_is_404(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={})
    _, status = ops.worker_artifact_urls(
        state,
        {"job_id": "missing", "worker_id": "w1", "run_id": "20260704-1", "files": ["report.html"]},
    )
    assert status == 404


def test_artifact_urls_missing_job_id_is_400(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    _, status = ops.worker_artifact_urls(state, {"run_id": "20260704-1", "files": ["report.html"]})
    assert status == 400


def test_artifact_urls_invalid_run_id_is_rejected(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    _, status = ops.worker_artifact_urls(
        state, {"job_id": "j1", "worker_id": "w1", "run_id": "../etc", "files": ["report.html"]}
    )
    assert status == 400


def test_artifact_urls_file_traversal_is_rejected(tmp_path: Path) -> None:
    # A worker-supplied key that escapes the run's namespace must fail, never be signed.
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    _, status = ops.worker_artifact_urls(
        state,
        {"job_id": "j1", "worker_id": "w1", "run_id": "20260704-1", "files": ["../../secret"]},
    )
    assert status == 400


def test_artifact_urls_files_must_be_a_list(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    _, status = ops.worker_artifact_urls(
        state, {"job_id": "j1", "worker_id": "w1", "run_id": "20260704-1", "files": "report.html"}
    )
    assert status == 400


def test_artifact_urls_rejects_a_worker_that_does_not_hold_the_lease(tmp_path: Path) -> None:
    # A worker relaying a job id it doesn't currently hold a lease on can't get signing URLs — so a
    # leaked/stale id can't be replayed to push objects into that job's prefix (409, like worker_result).
    repo = _FakeRepo({"j1": "acme"}, leased_by="other-worker")
    state = _state(tmp_path, store=_FakeStore(), repo=repo, jobs={"j1": "acme"})
    _, status = ops.worker_artifact_urls(
        state, {"job_id": "j1", "worker_id": "w1", "run_id": "20260704-1", "files": ["report.html"]}
    )
    assert status == 409


def test_artifact_urls_rejects_a_job_that_is_no_longer_leased(tmp_path: Path) -> None:
    # A done/failed job (no longer `leased`) can't be signed for, blocking a replay after the run.
    repo = _FakeRepo({"j1": "acme"}, status="done")
    state = _state(tmp_path, store=_FakeStore(), repo=repo, jobs={"j1": "acme"})
    _, status = ops.worker_artifact_urls(
        state, {"job_id": "j1", "worker_id": "w1", "run_id": "20260704-1", "files": ["report.html"]}
    )
    assert status == 409


def test_artifact_urls_missing_worker_id_is_400(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    _, status = ops.worker_artifact_urls(
        state, {"job_id": "j1", "run_id": "20260704-1", "files": ["report.html"]}
    )
    assert status == 400


# --- worker_scenario_url ------------------------------------------------------------------------


def test_no_object_store_returns_null_scenario_url(tmp_path: Path) -> None:
    state = _state(tmp_path, store=None, jobs={"j1": "default"})
    payload, status = ops.worker_scenario_url(
        state, {"job_id": "j1", "worker_id": "w1", "app": "demo", "ref": "login.yaml"}
    )
    assert status == 200
    assert payload == {"url": None}


def test_scenario_url_key_under_org_scenario_prefix(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "acme"})
    payload, status = ops.worker_scenario_url(
        state, {"job_id": "j1", "worker_id": "w1", "app": "demo", "ref": "login.yaml"}
    )
    assert status == 200
    # Assert the key (the real contract); the signed-in Content-Type is `content_type_for(ref)`,
    # whose value for `.yaml` depends on the runtime's mimetypes DB, so don't pin it here.
    assert payload["url"].startswith("https://signed.example/put/acme/scenarios/demo/login.yaml")


def test_scenario_url_rejects_an_unsafe_ref(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    _, status = ops.worker_scenario_url(
        state, {"job_id": "j1", "worker_id": "w1", "app": "demo", "ref": "../../etc/passwd.yaml"}
    )
    assert status == 400


def test_scenario_url_rejects_a_non_yaml_ref(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    _, status = ops.worker_scenario_url(
        state, {"job_id": "j1", "worker_id": "w1", "app": "demo", "ref": "login.txt"}
    )
    assert status == 400


def test_scenario_url_rejects_an_app_with_a_slash(tmp_path: Path) -> None:
    # `app` is a single key segment; a slash would let a worker climb into another project's slot.
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    _, status = ops.worker_scenario_url(
        state, {"job_id": "j1", "worker_id": "w1", "app": "demo/../other", "ref": "login.yaml"}
    )
    assert status == 400


def test_scenario_url_rejects_a_traversal_app(tmp_path: Path) -> None:
    # `..` without a slash still fails `valid_relative_key`, so a worker can't climb out that way.
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    _, status = ops.worker_scenario_url(
        state, {"job_id": "j1", "worker_id": "w1", "app": "..", "ref": "login.yaml"}
    )
    assert status == 400


def test_scenario_url_unknown_job_is_404(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={})
    _, status = ops.worker_scenario_url(
        state, {"job_id": "missing", "worker_id": "w1", "app": "demo", "ref": "login.yaml"}
    )
    assert status == 404


def test_scenario_url_missing_job_id_is_400(tmp_path: Path) -> None:
    state = _state(tmp_path, store=_FakeStore(), jobs={"j1": "default"})
    _, status = ops.worker_scenario_url(state, {"app": "demo", "ref": "login.yaml"})
    assert status == 400


# --- HTTP routes --------------------------------------------------------------------------------


def test_http_artifact_urls_route_signs_urls(tmp_path: Path) -> None:
    # Reachable through the real stdlib handler, not just the operation.
    state = ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path)
    state.object_store = _FakeStore()
    state.repository = _FakeRepo({"j1": "default"})  # type: ignore[assignment]
    server, port = _serve(state)
    try:
        status, resp = _post(
            port,
            "/api/worker/artifact-urls",
            {"job_id": "j1", "worker_id": "w1", "run_id": "20260704-1", "files": ["manifest.json"]},
        )
        assert status == 200
        assert resp["urls"]["manifest.json"].startswith(
            "https://signed.example/put/artifacts/20260704-1/manifest.json"
        )
    finally:
        server.shutdown()
        server.server_close()


def test_http_scenario_url_route_signs_url(tmp_path: Path) -> None:
    state = ServeState(runs_dir=tmp_path / "runs", cwd=tmp_path)
    state.object_store = _FakeStore()
    state.repository = _FakeRepo({"j1": "default"})  # type: ignore[assignment]
    server, port = _serve(state)
    try:
        status, resp = _post(
            port,
            "/api/worker/scenario-url",
            {"job_id": "j1", "worker_id": "w1", "app": "demo", "ref": "login.yaml"},
        )
        assert status == 200
        assert resp["url"].startswith("https://signed.example/put/scenarios/demo/login.yaml")
    finally:
        server.shutdown()
        server.server_close()
