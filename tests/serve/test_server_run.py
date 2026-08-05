"""Tests for the serve seam-assembly factory + the uvicorn (ASGI) transport (BE-0015, D2).

`serve` separates two choices: the **transport** (the stdlib server, or the FastAPI app over
uvicorn — `--asgi`) and the **backend** (which seam implementations the ServeState carries —
`--backend`, only `local` until the hosted backings land). `_build_state` is the seam-assembly
factory both share; `make_asgi_server` runs the FastAPI app as a real ASGI server. These tests
exercise the factory and a real uvicorn server (beyond the TestClient coverage in
test_server_app.py), so the actual serving path — middleware, routing — is checked end to end.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from _shared import patch_gcs_client, project

from bajutsu import serve as srv


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _state(tmp_path: Path) -> srv.ServeState:
    _scn, cfg, runs = project(tmp_path)
    return srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
    )


def test_build_state_assembles_local_seams(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert isinstance(state, srv.ServeState)
    assert isinstance(state.executor, srv.LocalExecutor)
    assert isinstance(state.logbus, srv.InMemoryLogBus)
    assert isinstance(state.artifacts, srv.LocalArtifactStore)
    assert isinstance(state.scenarios, srv.LocalScenarioStore)


def test_build_state_rejects_unknown_backend(tmp_path: Path) -> None:
    _scn, cfg, runs = project(tmp_path)
    # An unknown backend fails loudly rather than silently running local.
    with pytest.raises(ValueError, match="backend"):
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="bogus",
        )


def test_build_state_server_wires_the_hosted_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
    from bajutsu.serve.server.baselines import ObjectBaselineStore
    from bajutsu.serve.server.db_executor import DbQueueExecutor
    from bajutsu.serve.server.post_completion_logbus import PostCompletionLogBus
    from bajutsu.serve.server.scenarios import StorageScenarioStore
    from bajutsu.serve.server.secrets import DbSecretStore

    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", Fernet.generate_key().decode("ascii"))
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert isinstance(state.executor, DbQueueExecutor)
    assert isinstance(state.logbus, PostCompletionLogBus)
    assert isinstance(state.artifacts, ObjectStorageArtifactStore)
    assert isinstance(state.scenarios, StorageScenarioStore)
    assert isinstance(state.baselines, ObjectBaselineStore)
    assert isinstance(state.secrets, DbSecretStore)  # encrypted per-org store (BE-0136)
    scope = state.scenarios.scope("demo")
    assert scope is not None
    # The scenario lives on `demo`'s configured local dir (`project`'s fixture), never in the fake
    # bucket: list/read resolve it from there without touching object storage (BE-0324).
    assert [s["file"] for s in scope.list()] == ["smoke.yaml"]
    assert scope.read("smoke.yaml") == (_scn / "smoke.yaml").read_text(encoding="utf-8")


def test_build_state_local_has_no_repository(tmp_path: Path) -> None:
    # The system of record is server-only; local never has one, so its behavior is unchanged.
    assert _state(tmp_path).repository is None


def test_build_state_server_has_no_repository_without_a_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The database is optional on the server backend: with BAJUTSU_DATABASE_URL unset the repository
    # stays None, so the existing server backing keeps working until a database is configured.
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.repository is None


def test_build_state_server_wires_the_repository_when_a_database_url_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With BAJUTSU_DATABASE_URL set, the server backend assembles a SqlRepository (SQLite here, so
    # it builds on the gate without a live Postgres).
    from cryptography.fernet import Fernet

    from bajutsu.serve.server.db import SqlRepository

    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", Fernet.generate_key().decode("ascii"))
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert isinstance(state.repository, SqlRepository)


def test_build_state_server_requires_a_secrets_key_with_a_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A database-backed server persists operator secrets encrypted, so the master key must be
    # provisioned — assembly fails loudly rather than degrading to plaintext-in-memory (BE-0136).
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    monkeypatch.delenv("BAJUTSU_SECRETS_KEY", raising=False)
    _scn, cfg, runs = project(tmp_path)
    with pytest.raises(ValueError, match="BAJUTSU_SECRETS_KEY"):
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="server",
        )


def test_build_state_local_uses_in_memory_sessions(tmp_path: Path) -> None:
    # Local sessions stay in-memory (a restart drops them), so its behavior is unchanged.
    from bajutsu.serve.sessions import InMemorySessionStore

    assert isinstance(_state(tmp_path).auth.sessions, InMemorySessionStore)


def test_build_state_server_uses_sql_sessions_when_db_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    from bajutsu.serve.server.sessions import SqlSessionStore

    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", Fernet.generate_key().decode("ascii"))
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert isinstance(state.auth.sessions, SqlSessionStore)


def test_build_state_server_falls_back_to_in_memory_sessions_without_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.serve.sessions import InMemorySessionStore

    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert isinstance(state.auth.sessions, InMemorySessionStore)


def test_build_state_local_has_no_oauth(tmp_path: Path) -> None:
    # OAuth is server-only; local never has it (token auth only), so behavior is unchanged.
    state = _state(tmp_path)
    assert state.auth.oauth is None
    assert state.auth.oauth_admin_teams == ()


def test_build_state_server_wires_oauth_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bajutsu.serve.server.oauth import GitHubOAuthClient

    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("BAJUTSU_OAUTH_GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("BAJUTSU_OAUTH_GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv(
        "BAJUTSU_OAUTH_GITHUB_REDIRECT_URI", "https://app.example/api/oauth/callback"
    )
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert isinstance(state.auth.oauth, GitHubOAuthClient)


def test_build_state_server_parses_the_admin_teams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The admin role follows one or more server-wide GitHub Teams, named by the comma-separated
    # BAJUTSU_OAUTH_ADMIN_TEAMS.
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("BAJUTSU_OAUTH_ADMIN_TEAMS", "acme-gh/ops, other-gh/root")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.auth.oauth_admin_teams == ("acme-gh/ops", "other-gh/root")


def _setenv_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAJUTSU_OAUTH_GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("BAJUTSU_OAUTH_GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv(
        "BAJUTSU_OAUTH_GITHUB_REDIRECT_URI", "https://app.example/api/oauth/callback"
    )


def _delenv_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    # The mirror image of _setenv_oauth: a "no warning" test's premise is that OAuth isn't wired
    # (or that a name isn't set), which the ambient shell can silently falsify for a maintainer who
    # exports these for their own deployment -- pin the premise instead of inheriting it.
    for var in (
        "BAJUTSU_OAUTH_GITHUB_CLIENT_ID",
        "BAJUTSU_OAUTH_GITHUB_CLIENT_SECRET",
        "BAJUTSU_OAUTH_GITHUB_REDIRECT_URI",
        "BAJUTSU_OAUTH_ADMIN_TEAM",
        "BAJUTSU_OAUTH_ADMIN_TEAMS",
    ):
        monkeypatch.delenv(var, raising=False)


def test_build_state_server_warns_on_the_retired_singular_admin_team_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A deployment still on the old BAJUTSU_OAUTH_ADMIN_TEAM (no BAJUTSU_OAUTH_ADMIN_TEAMS) would
    # otherwise lose every admin silently on this hard cutover — it must warn loudly instead.
    # OAuth must actually be wired (all three GitHub vars set), since the warning fires only then.
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _setenv_oauth(monkeypatch)
    monkeypatch.delenv("BAJUTSU_OAUTH_ADMIN_TEAMS", raising=False)
    monkeypatch.setenv("BAJUTSU_OAUTH_ADMIN_TEAM", "acme-gh/ops")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.auth.oauth_admin_teams == ()
    err = capsys.readouterr().err
    assert "BAJUTSU_OAUTH_ADMIN_TEAMS is empty" in err
    assert "BAJUTSU_OAUTH_ADMIN_TEAM is retired" in err


def test_build_state_server_warns_when_admin_teams_was_never_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The likelier miss than the retired name: a deployment that never set either variable (a
    # fresh OAuth setup, or an upgrade that dropped the old name without adding the new one) --
    # falls through both the old guard's own condition and the malformed-entry check, since an
    # empty list has no entries to be malformed.
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _setenv_oauth(monkeypatch)
    monkeypatch.delenv("BAJUTSU_OAUTH_ADMIN_TEAM", raising=False)
    monkeypatch.delenv("BAJUTSU_OAUTH_ADMIN_TEAMS", raising=False)
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.auth.oauth_admin_teams == ()
    err = capsys.readouterr().err
    assert "BAJUTSU_OAUTH_ADMIN_TEAMS is empty" in err
    assert "retired" not in err


def test_build_state_server_without_oauth_does_not_warn_about_admin_teams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Token-auth-only server backend: BAJUTSU_OAUTH_ADMIN_TEAMS has no meaning without OAuth wired,
    # so the empty-list warning must stay quiet rather than tell every such deployment it has no
    # admin -- the guard's first operand, which no other test in this file exercises.
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _delenv_oauth(monkeypatch)
    _scn, cfg, runs = project(tmp_path)
    srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert "BAJUTSU_OAUTH_ADMIN_TEAMS" not in capsys.readouterr().err


def test_build_state_server_without_oauth_does_not_warn_on_malformed_admin_teams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Same gating for the malformed-entry check: without OAuth wired, BAJUTSU_OAUTH_ADMIN_TEAMS
    # decides nothing, so a stale malformed value left in the environment must not warn either.
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _delenv_oauth(monkeypatch)
    monkeypatch.setenv("BAJUTSU_OAUTH_ADMIN_TEAMS", "acme-gh/ops other-gh/root")
    _scn, cfg, runs = project(tmp_path)
    srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert "BAJUTSU_OAUTH_ADMIN_TEAMS" not in capsys.readouterr().err


def test_build_state_server_stays_quiet_when_admin_teams_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A deployment carrying both vars across the upgrade (the new one wins) must not warn either —
    # set the retired singular name too, and wire OAuth (the warnings fire only then), so this
    # actually exercises the guard's other operand instead of passing on OAuth being unwired.
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _setenv_oauth(monkeypatch)
    monkeypatch.setenv("BAJUTSU_OAUTH_ADMIN_TEAM", "acme-gh/legacy")
    monkeypatch.setenv("BAJUTSU_OAUTH_ADMIN_TEAMS", "acme-gh/ops")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.auth.oauth_admin_teams == ("acme-gh/ops",)  # the new name wins, the old is ignored
    assert "BAJUTSU_OAUTH_ADMIN_TEAM" not in capsys.readouterr().err


def test_build_state_server_warns_on_a_malformed_admin_teams_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A space- or semicolon-separated value parses to one entry that still contains "/" — a bare
    # "/" count would pass it — yet it can never match a real "<github-org>/<team-slug>": that
    # silently loses every admin the same way the retired singular name does, so it must warn too.
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _setenv_oauth(monkeypatch)
    monkeypatch.setenv("BAJUTSU_OAUTH_ADMIN_TEAMS", "acme-gh/ops other-gh/root")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.auth.oauth_admin_teams == ("acme-gh/ops other-gh/root",)
    assert "will never match" in capsys.readouterr().err


def test_build_state_server_warns_on_an_empty_side_or_inner_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Each of these has exactly one "/" (so a bare count would pass it) but can never match a
    # real "<github-org>/<team-slug>": an empty org half, an empty slug half, and a slug half with
    # a stray leading space that `.strip()` on the whole entry doesn't remove.
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _setenv_oauth(monkeypatch)
    monkeypatch.setenv("BAJUTSU_OAUTH_ADMIN_TEAMS", "acme-gh/,/ops,acme-gh/ ops")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.auth.oauth_admin_teams == ("acme-gh/", "/ops", "acme-gh/ ops")
    assert "will never match" in capsys.readouterr().err


def test_build_state_server_does_not_warn_on_an_uppercase_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # GitHub always lowercases a Team's slug, so an operator who copies the Team name as shown in
    # the UI (title case) writes an entry whose case differs from the stored slug -- but
    # `in_admin_team` case-folds both sides before comparing, so this entry matches a real Team
    # `acme-gh/ops` perfectly. Warning here would tell the operator to "fix" a working config, and
    # teach them to ignore this warning for the one list where a genuinely broken entry hides.
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _setenv_oauth(monkeypatch)
    monkeypatch.setenv("BAJUTSU_OAUTH_ADMIN_TEAMS", "acme-gh/Ops")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.auth.oauth_admin_teams == ("acme-gh/Ops",)
    assert "will never match" not in capsys.readouterr().err


def test_build_state_server_parses_the_per_user_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("BAJUTSU_MAX_CONCURRENT_PER_USER", "3")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.max_concurrent_per_user == 3


def test_build_state_server_parses_the_per_org_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("BAJUTSU_MAX_CONCURRENT_PER_ORG", "5")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.max_concurrent_per_org == 5


def test_build_state_server_has_no_oauth_without_the_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BAJUTSU_OAUTH_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setenv("BAJUTSU_REDIS_URL", "redis://localhost:6379")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert state.auth.oauth is None


def test_build_state_server_requires_a_server_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BAJUTSU_SERVER_STORE", raising=False)
    _scn, cfg, runs = project(tmp_path)
    with pytest.raises(ValueError, match="BAJUTSU_SERVER_STORE"):
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="server",
        )


def test_build_state_server_without_extras_raises_a_clear_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With an extra missing, the server assembly surfaces one install hint naming the extras — not
    # a raw ImportError for whichever module loaded first. We block a sub-import the server path
    # needs (the object-store client) so ImportError fires before any env check.
    import sys

    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt")
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    monkeypatch.setitem(sys.modules, "boto3", None)
    _scn, cfg, runs = project(tmp_path)
    with pytest.raises(srv.MissingServerExtra, match="extra"):
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="server",
        )


def test_build_state_server_without_the_gcs_extra_names_it_in_the_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The generic install hint predates GCS support and only ever needed to cover boto3 (bundled in
    # the `server` extra) — it must also name `gcs` now that a gs:// BAJUTSU_SERVER_STORE is a real,
    # separately-installed path (BE-0204), or the printed command doesn't actually fix the problem.
    import sys

    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "gs://bkt")
    monkeypatch.setitem(sys.modules, "google", None)
    _scn, cfg, runs = project(tmp_path)
    with pytest.raises(srv.MissingServerExtra, match="gcs"):
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="server",
        )


def test_build_state_server_rejects_a_malformed_server_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A malformed BAJUTSU_SERVER_STORE names the actual setting, not the sibling --evidence-store
    # wording `evidence_target_from_uri` raises internally (BE-0204).
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "bkt")  # no s3:// or gs:// scheme
    _scn, cfg, runs = project(tmp_path)
    with pytest.raises(ValueError, match="BAJUTSU_SERVER_STORE 'bkt' is invalid"):
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="server",
        )


def test_build_state_server_reraises_internal_import_bugs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed bajutsu.* import is a real bug, not a missing extra: it must surface unchanged rather
    # than be rewritten into the install hint (which would hide the traceback). Only third-party
    # extras get the hint.
    def boom(**_kwargs: object) -> srv.ServeState:
        raise ImportError("cannot import name 'X'", name="bajutsu.serve.server.db")

    monkeypatch.setattr(srv, "_build_server_state", boom)
    _scn, cfg, runs = project(tmp_path)
    with pytest.raises(ImportError) as caught:
        srv._build_state(
            runs_dir=runs,
            config=cfg,
            scenarios_dir=None,
            root=tmp_path,
            baselines_dir=None,
            max_concurrent=4,
            token=None,
            backend="server",
        )
    assert not isinstance(caught.value, srv.MissingServerExtra)
    assert "extra" not in str(caught.value)


def test_build_state_server_normalizes_a_prefix_without_a_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A prefix without a trailing slash must not fuse into "tenantartifacts/" / "tenantscenarios/".
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "s3://bkt/tenant")  # no trailing slash
    monkeypatch.setenv("BAJUTSU_S3_REGION", "auto")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    # The artifact store keys a run-relative path under "<prefix>artifacts/", with the slash added.
    assert state.artifacts._key("r1/report.html") == "tenant/artifacts/r1/report.html"


def test_build_state_server_wires_a_gcs_object_store_from_a_gs_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A gs:// BAJUTSU_SERVER_STORE builds the server backend on GCSObjectStore instead of S3 (BE-0204)
    # — a fake storage.Client keeps the real object_store_from_uri path off the network.
    from bajutsu.object_store import GCSObjectStore

    patch_gcs_client(monkeypatch)
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "gs://bucket/tenant")
    _scn, cfg, runs = project(tmp_path)
    state = srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=4,
        token=None,
        backend="server",
    )
    assert isinstance(state.artifacts._store, GCSObjectStore)
    assert state.artifacts._key("r1/report.html") == "tenant/artifacts/r1/report.html"


def test_asgi_server_serves_the_app_over_a_real_socket(tmp_path: Path) -> None:
    state = _state(tmp_path)
    port = _free_port()
    server = srv.make_asgi_server(state, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started, "uvicorn server did not start"
        resp = httpx.get(f"http://127.0.0.1:{port}/api/runs", timeout=5)
        assert resp.status_code == 200 and resp.json() == []
        # The hardening middleware runs on the real server too (not just under TestClient).
        assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "uvicorn server did not shut down"


def test_sse_route_delivers_keepalive_over_the_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # End to end over a real uvicorn server: an idle (running, not closed) job's /events stream
    # delivers the log frame and periodic `:keepalive` comments, so a proxy won't drop the idle
    # connection (B). Breaking out closes the socket — exercising the route's disconnect path.
    from bajutsu.serve.server import app as app_module

    monkeypatch.setattr(app_module, "_SSE_KEEPALIVE", 0.05)
    state = _state(tmp_path)
    state.jobs["w"] = srv.Job(id="w", cmd=[])
    state.logbus.publish(
        "w", "line one\n"
    )  # a line, but the job is NOT closed -> stream stays open
    port = _free_port()
    server = srv.make_asgi_server(state, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started
        seen_line = seen_keepalive = False
        with httpx.stream("GET", f"http://127.0.0.1:{port}/api/jobs/w/events", timeout=5) as r:
            for line in r.iter_lines():
                if "data: line one" in line:
                    seen_line = True
                if line.startswith(":keepalive"):
                    seen_keepalive = True
                if seen_line and seen_keepalive:
                    break
        assert seen_line and seen_keepalive
    finally:
        state.logbus.close("w")
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "uvicorn server did not shut down"
