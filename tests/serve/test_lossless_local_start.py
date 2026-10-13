"""A local start loses nothing the former local backend offered (BE-0449 unit 5, part 1).

`bajutsu serve` with no cloud service is the shape the local backend moved to. These tests lock
what it keeps: the capture routes and the capture capability, the UI-set secrets that a worker's
lease relays to a job, scenarios and baselines on the local tree, and the saved AI provider
settings file. The secret read that makes the second possible is locked off every HTTP handler.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from _shared import SCENARIO, FakeObjectStore, project
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from bajutsu import serve as srv
from bajutsu.serve import jobs
from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import _spawn_env, job_secret_env
from bajutsu.serve.operations.config import (
    AI_API_KEY_SECRET,
    AI_CLAUDE_CODE_TOKEN_SECRET,
    GIT_CONFIG_TOKEN_SECRET,
    restore_persisted_provider_settings,
)
from bajutsu.serve.secrets import EnvSecretStore
from bajutsu.serve.server.app import make_app
from bajutsu.serve.server.worker_job import job_spec
from bajutsu.serve.state import Job
from bajutsu.serve.upload_artifacts import artifact_store_key

_REPO = Path(__file__).resolve().parents[2]

# Every variable that would steer the server backend away from its local defaults, plus the
# credentials a test sets through the API and must not find already in the environment.
_ENV = (
    "BAJUTSU_SERVER_STORE",
    "BAJUTSU_DATABASE_URL",
    "BAJUTSU_SECRETS_KEY",
    "BAJUTSU_HOSTED",
    "BAJUTSU_OAUTH_GITHUB_CLIENT_ID",
    "BAJUTSU_OAUTH_GITHUB_CLIENT_SECRET",
    "BAJUTSU_OAUTH_GITHUB_REDIRECT_URI",
    "BAJUTSU_OIDC_AUDIENCE",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "BAJUTSU_GIT_CONFIG_TOKEN",
    "LOGIN_PASSWORD",
)


@pytest.fixture(autouse=True)
def _no_server_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)


def _project(tmp_path: Path) -> Path:
    """The shared project, with the `demo` target declaring a scenario secret."""
    scn_dir, cfg, _runs = project(tmp_path)
    cfg.write_text(
        "defaults: { backend: [ios] }\ntargets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn_dir}, "
        "secrets: [LOGIN_PASSWORD] }\n",
        encoding="utf-8",
    )
    return scn_dir


def _local(tmp_path: Path) -> srv.ServeState:
    return srv._build_state(
        runs_dir=tmp_path / "runs",
        config=tmp_path / "bajutsu.config.yaml",
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=tmp_path / "baselines",
        max_concurrent=4,
        token=None,
    )


class _Recording:
    """An executor that keeps what it is handed, so a dispatch test stays synchronous."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def dispatch(self, _state: srv.ServeState, job: Job) -> None:
        self.jobs.append(job)


# --- end to end: a local start through the FastAPI app ---


def test_a_local_start_keeps_what_the_local_backend_offers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scn_dir = _project(tmp_path)
    # Provider settings a user saved under the former local backend (BE-0184), before this start.
    settings_file = tmp_path / "provider-settings.json"
    settings_file.write_text(
        json.dumps({"provider": "api-key", "settings": {"api-key": {"effort": "high"}}}),
        encoding="utf-8",
    )
    state = _local(tmp_path)
    restore_persisted_provider_settings(state)
    client = TestClient(make_app(state))

    # Capture: the capability is on and the routes reach their operations rather than a 404.
    capture = client.get("/api/config").json()["capabilities"]["capture"]
    assert capture == {"available": True, "reason": None}
    assert client.post("/api/capture/start", json={}).status_code == 400
    assert client.get("/api/capture/screenshot").status_code == 404
    assert client.get("/api/capture/screenshot").json() == {"error": "no active capture session"}

    # A scenario saved through the API lands in the local tree and lists back from it.
    saved = client.post(
        "/api/scenario", json={"target": "demo", "path": "fresh.yaml", "yaml": SCENARIO}
    )
    assert saved.status_code == 200, saved.text
    assert (scn_dir / "fresh.yaml").read_text(encoding="utf-8") == SCENARIO
    listed = client.get("/api/scenarios", params={"target": "demo"}).json()
    assert "fresh.yaml" in {s["file"] for s in listed}
    assert not (tmp_path / ".bajutsu" / "objects" / "default" / "scenarios").exists()

    # An approved baseline lands in the directory a run is pointed at.
    shot = tmp_path / "runs" / "20260101-000000" / "s1" / "visual-actual.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG approved")
    approved = client.post(
        "/api/approve", json={"runId": "20260101-000000", "sid": "s1", "baseline": "home.png"}
    )
    assert approved.status_code == 200, approved.text
    assert (state.baselines_dir / "home.png").read_bytes() == b"\x89PNG approved"
    queue = _Recording()
    state.executor = queue
    started = client.post("/api/run", json={"target": "demo", "scenario": "smoke.yaml"})
    assert started.status_code == 200, started.text
    cmd = queue.jobs[0].cmd
    assert cmd[cmd.index("--baselines") + 1] == str(state.baselines_dir)
    # The scenario is the local file itself, so nothing has to travel as materials.
    assert queue.jobs[0].materials == {}

    # The saved provider settings carried over, and a change writes back to the same file.
    assert client.get("/api/provider").json()["provider"] == "api-key"
    assert queue.jobs[0].env_overlay.get("BAJUTSU_AI_EFFORT") == "high"
    changed = client.post("/api/provider", json={"provider": "api-key", "effort": "low"})
    assert changed.status_code == 200, changed.text
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["settings"]["api-key"]["effort"] == "low"

    # Secrets set through the API reach a job through the lease that hands it to a worker.
    assert client.post("/api/apikey", json={"value": "sk-ant-local-0001"}).status_code == 200
    assert (
        client.post(
            "/api/secrets", json={"name": "LOGIN_PASSWORD", "value": "hunter2 local"}
        ).status_code
        == 200
    )
    relayed = jobs.lease_secret_env(state, "default")
    assert relayed["ANTHROPIC_API_KEY"] == "sk-ant-local-0001"
    assert relayed["LOGIN_PASSWORD"] == "hunter2 local"


def test_no_http_response_carries_a_stored_secret(tmp_path: Path) -> None:
    _project(tmp_path)
    client = TestClient(make_app(_local(tmp_path)))
    client.post("/api/apikey", json={"value": "sk-ant-local-0002"})
    client.post("/api/secrets", json={"name": "LOGIN_PASSWORD", "value": "hunter2-secret"})
    for path in ("/api/apikey", "/api/secrets", "/api/config", "/api/server", "/api/provider"):
        body = client.get(path).text
        assert "sk-ant-local-0002" not in body
        assert "hunter2-secret" not in body


def test_another_org_keeps_the_bucket_layout(tmp_path: Path) -> None:
    from bajutsu.serve.server.baselines import ObjectBaselineStore
    from bajutsu.serve.server.scenarios import StorageScenarioStore

    _project(tmp_path)
    state = _local(tmp_path)
    assert state.repository is not None
    state.repository.ensure_org("acme", slug="acme", name="acme")
    acme = state.for_org("acme")
    # Only the default org's stores are the local tree; another tenant writes nothing into it.
    assert isinstance(acme.scenarios, StorageScenarioStore)
    assert isinstance(acme.baselines, ObjectBaselineStore)
    assert acme.provider_settings is not state.for_org("default").provider_settings


def test_a_hosted_server_store_keeps_its_bucket_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.serve.server.baselines import ObjectBaselineStore
    from bajutsu.serve.server.scenarios import StorageScenarioStore

    _project(tmp_path)
    monkeypatch.setattr(
        "bajutsu.serve.server.local_start.object_store_from_env",
        lambda: (FakeObjectStore(), "p"),
    )
    # A bucket needs a database for its queue (BE-0449 unit 9).
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", Fernet.generate_key().decode("ascii"))
    state = _local(tmp_path)
    default = state.for_org("default")
    assert isinstance(default.scenarios, StorageScenarioStore)
    assert isinstance(default.baselines, ObjectBaselineStore)


# --- the secret read for a job ---


def test_db_secret_store_values_for_job_decrypts_its_own_org_only(tmp_path: Path) -> None:
    from sqlalchemy import create_engine

    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.secrets import DbSecretStore

    engine = create_engine(f"sqlite:///{tmp_path / 'q.db'}")
    from bajutsu.serve.server.models import Base

    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    for org in ("default", "acme"):
        repo.ensure_org(org, slug=org, name=org)
    fernet = Fernet(Fernet.generate_key())
    DbSecretStore(engine, "default", fernet).set(AI_API_KEY_SECRET, "sk-default")
    DbSecretStore(engine, "acme", fernet).set(AI_API_KEY_SECRET, "sk-acme")
    DbSecretStore(engine, "acme", fernet).set("LOGIN_PASSWORD", "pw-acme")
    assert DbSecretStore(engine, "default", fernet).values_for_job() == {
        AI_API_KEY_SECRET: "sk-default"
    }
    assert DbSecretStore(engine, "acme", fernet).values_for_job() == {
        AI_API_KEY_SECRET: "sk-acme",
        "LOGIN_PASSWORD": "pw-acme",
    }


def test_db_secret_store_skips_a_value_the_key_cannot_decrypt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from sqlalchemy import create_engine

    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base
    from bajutsu.serve.server.secrets import DbSecretStore

    engine = create_engine(f"sqlite:///{tmp_path / 'q.db'}")
    Base.metadata.create_all(engine)
    SqlRepository(engine).ensure_org("default", slug="default", name="default")
    DbSecretStore(engine, "default", Fernet(Fernet.generate_key())).set("OLD", "stale")
    rotated = DbSecretStore(engine, "default", Fernet(Fernet.generate_key()))
    rotated.set("NEW", "fresh")
    with caplog.at_level("WARNING"):
        assert rotated.values_for_job() == {"NEW": "fresh"}
    assert "'OLD'" in caplog.text
    assert "stale" not in caplog.text


def test_env_secret_store_values_for_job_names_what_it_wrote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("X_VAR", raising=False)
    monkeypatch.delenv("Y_VAR", raising=False)
    store = EnvSecretStore(lambda name: f"{name}_VAR")
    assert store.values_for_job() == {}
    store.set("X", "one")
    store.set("Y", "two")
    store.set("Y", "")
    assert store.values_for_job() == {"X": "one"}
    # A value something else removed from the environment is gone for the job too.
    monkeypatch.delenv("X_VAR")
    assert store.values_for_job() == {}


def test_job_secret_env_maps_names_to_variables_and_leaves_the_git_credential_out(
    tmp_path: Path,
) -> None:
    _project(tmp_path)
    state = _local(tmp_path)
    secrets = state.for_org("default").secrets
    secrets.set(AI_API_KEY_SECRET, "sk-ant-0003")
    secrets.set(AI_CLAUDE_CODE_TOKEN_SECRET, "oauth-0003")
    secrets.set(GIT_CONFIG_TOKEN_SECRET, "ghp-0003")
    secrets.set("LOGIN_PASSWORD", "pw-0003")
    assert job_secret_env(state, "default") == {
        "ANTHROPIC_API_KEY": "sk-ant-0003",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-0003",
        "LOGIN_PASSWORD": "pw-0003",
    }


def test_job_secret_env_keeps_orgs_apart(tmp_path: Path) -> None:
    _project(tmp_path)
    state = _local(tmp_path)
    assert state.repository is not None
    state.repository.ensure_org("acme", slug="acme", name="acme")
    state.for_org("acme").secrets.set("LOGIN_PASSWORD", "acme-only")
    assert job_secret_env(state, "default") == {}
    assert job_secret_env(state, "acme") == {"LOGIN_PASSWORD": "acme-only"}


def test_the_provider_overlay_stays_authoritative_over_a_secret() -> None:
    job = Job(
        secret_env={"LOGIN_PASSWORD": "pw", "BAJUTSU_AI_PROVIDER": "from-secret"},
        env_overlay={"BAJUTSU_AI_PROVIDER": "bedrock"},
    )
    env = _spawn_env(job)
    assert env["LOGIN_PASSWORD"] == "pw"
    assert env["BAJUTSU_AI_PROVIDER"] == "bedrock"


def test_a_jobs_secrets_stay_out_of_its_repr_and_its_stored_spec() -> None:
    job = Job(cmd=["x"], secret_env={"LOGIN_PASSWORD": "never-logged"})
    assert "never-logged" not in repr(job)
    assert "never-logged" not in json.dumps(job_spec(job))


def _calls_of(name: str) -> set[str]:
    """Every module under `bajutsu/` that calls *name*, as a repo-relative path."""
    found: set[str] = set()
    for path in (_REPO / "bajutsu").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called == name:
                found.add(path.relative_to(_REPO).as_posix())
    return found


def test_only_the_job_execution_path_reads_a_secrets_plaintext() -> None:
    # BE-0136 keeps every plaintext read away from an HTTP handler. The one read (BE-0449) is called
    # from `serve.jobs` alone, and `job_secret_env` from there too: the lease's own named reader
    # (`test_local_start_worker`) goes through it. This is a tripwire
    # on direct calls by name, not a lock: a call through an alias or `getattr` would slip past it.
    # It catches the likely mistake, an operation or route calling either function outright.
    assert _calls_of("values_for_job") == {"bajutsu/serve/jobs.py"}
    assert _calls_of("job_secret_env") == {"bajutsu/serve/jobs.py"}


def test_a_worker_downloads_the_baseline_an_approve_wrote(tmp_path: Path) -> None:
    # An override run materializes the org's baselines on the worker from signed URLs. On a local
    # start those URLs read the `--baselines` directory an approve writes, so both name one file.
    scn_dir, cfg, _runs = project(tmp_path)
    cfg.write_text(
        "defaults: { backend: [ios] }\ntargets:\n"
        f"  demo: {{ bundleId: com.example.demo, appPath: build/Demo.app, scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    state = _local(tmp_path)
    client = TestClient(make_app(state))
    shot = tmp_path / "runs" / "20260101-000000" / "s1" / "visual-actual.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG approved")
    approved = client.post(
        "/api/approve", json={"runId": "20260101-000000", "sid": "s1", "baseline": "home.png"}
    )
    assert approved.status_code == 200, approved.text

    binary = b"app binary"
    sha = hashlib.sha256(binary).hexdigest()
    assert state.object_store is not None
    state.object_store.put_bytes(artifact_store_key("", "default", "binary", sha), binary)
    started = client.post(
        "/api/run", json={"target": "demo", "scenario": "smoke.yaml", "binaryArtifact": sha}
    )
    assert started.status_code == 200, started.text
    job = next(iter(state.jobs.values()))
    assert job.materialize_baselines is True

    leased, code = ops.worker_lease(state, "w1", job.capabilities)
    assert code == 200
    assert set(leased["baseline_urls"]) == {"home.png"}
    url = urlsplit(leased["baseline_urls"]["home.png"])
    download = client.get(f"{url.path}?{url.query}")
    assert download.status_code == 200
    assert download.content == b"\x89PNG approved"


# --- names and directories that would reach the default org's tree ---


@pytest.mark.parametrize("slug", ["artifacts", "baselines", "scenarios", "uploads"])
def test_the_api_refuses_an_org_slug_the_default_layout_uses(tmp_path: Path, slug: str) -> None:
    # A non-default org's objects nest under its slug, so `baselines` would write into the
    # `--baselines` directory and `artifacts` into the runs tree.
    _project(tmp_path)
    state = _local(tmp_path)
    body, code = ops.create_org(state, {"slug": slug})
    assert code == 400
    assert "reserved" in body["error"]
    assert state.repository is not None and state.repository.get_org(slug) is None


def test_a_config_org_named_like_a_store_folder_is_never_seeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.serve.operations.config import seed_orgs_from_bound_config
    from bajutsu.serve.orgs import load_serve_config

    text = "targets:\n  demo: { bundleId: com.example.demo }\norgs:\n  baselines: { members: [alice] }\n"
    with pytest.raises(ValueError, match="'baselines' is reserved"):
        load_serve_config(text)
    _project(tmp_path)
    (tmp_path / "bajutsu.config.yaml").write_text(text, encoding="utf-8")
    state = _local(tmp_path)
    seed_orgs_from_bound_config(state)
    assert state.repository is not None and state.repository.get_org("baselines") is None


@pytest.mark.parametrize("baselines", [".", "runs", ".bajutsu"])
def test_a_local_start_refuses_baselines_that_hold_the_runs_or_state_directory(
    tmp_path: Path, baselines: str
) -> None:
    # The store serves the baselines directory over signed URLs, so one enclosing the state
    # directory would hand out the signing key, the secrets key, and the queue database.
    _project(tmp_path)
    with pytest.raises(ValueError, match="--baselines"):
        srv._build_state(
            runs_dir=tmp_path / "runs",
            config=tmp_path / "bajutsu.config.yaml",
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=tmp_path / baselines,
            max_concurrent=4,
            token=None,
        )
