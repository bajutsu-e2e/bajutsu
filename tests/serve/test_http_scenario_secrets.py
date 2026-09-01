"""The serve scenario-secrets endpoints (BE-0274) over a real ThreadingHTTPServer.

A scenario declares the secret env-var names it needs under a config's ``secrets:`` (BE-0032);
``${secrets.X}`` resolves them from the environment at run time. These endpoints let an operator
provision those values from the Web UI, reusing the write-once ``SecretStore`` seam (BE-0136): a
value is stored masked and never read back, and a spawned run inherits it under its declared name.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _shared import (
    _get_json,
    _post,
    _serve,
)

from bajutsu import serve as srv
from bajutsu.serve.jobs import _spawn_env
from bajutsu.serve.operations.config import (
    AI_API_KEY_SECRET,
    AI_CLAUDE_CODE_TOKEN_SECRET,
    GIT_CONFIG_TOKEN_SECRET,
)


def _project_with_secrets(tmp_path: Path, *, demo_secrets: str, other_secrets: str = "") -> Path:
    """A config whose two targets each declare their own ``secrets:`` list, so the union across
    targets (BE-0274 item 1) is exercised. *demo_secrets*/*other_secrets* are inline YAML lists."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: a\n  steps:\n    - tap: { id: x }\n")
    cfg = tmp_path / "bajutsu.config.yaml"
    other = f", secrets: {other_secrets}" if other_secrets else ""
    cfg.write_text(
        "defaults: { backend: [ios] }\n"
        "targets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn_dir}, secrets: {demo_secrets} }}\n"
        f"  other: {{ bundleId: com.example.other{other} }}\n",
        encoding="utf-8",
    )
    return cfg


def _state(tmp_path: Path, cfg: Path) -> srv.ServeState:
    runs = tmp_path / "runs"
    runs.mkdir()
    return srv.ServeState(
        scenarios_dir=tmp_path / "scenarios", config=cfg, runs_dir=runs, cwd=tmp_path
    )


def test_get_reflects_declared_names_union_across_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/secrets lists every name the bound config declares, unioned across targets, each
    unset and carrying no plaintext value."""
    monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
    monkeypatch.delenv("LOGIN_USER", raising=False)
    monkeypatch.delenv("OTHER_TOKEN", raising=False)
    cfg = _project_with_secrets(
        tmp_path, demo_secrets="[LOGIN_PASSWORD, LOGIN_USER]", other_secrets="[OTHER_TOKEN]"
    )
    server, port = _serve(_state(tmp_path, cfg))
    try:
        body = _get_json(port, "/api/secrets")
        assert [e["name"] for e in body] == ["LOGIN_PASSWORD", "LOGIN_USER", "OTHER_TOKEN"]
        assert all(e["set"] is False for e in body)
        assert all("value" not in e for e in body)  # describe-only: never the plaintext
    finally:
        server.shutdown()
        server.server_close()


def test_get_empty_when_no_secrets_declared(tmp_path: Path) -> None:
    """A config that declares no ``secrets:`` yields an empty list — nothing to configure."""
    cfg = _project_with_secrets(tmp_path, demo_secrets="[]")
    server, port = _serve(_state(tmp_path, cfg))
    try:
        assert _get_json(port, "/api/secrets") == []
    finally:
        server.shutdown()
        server.server_close()


def test_unsafe_declared_name_is_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A config that declares a system variable (PATH) as a secret never offers it — the
    ``_valid_key_env_name`` guard drops it from the list (and from the settable set)."""
    monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
    cfg = _project_with_secrets(tmp_path, demo_secrets="[LOGIN_PASSWORD, PATH]")
    server, port = _serve(_state(tmp_path, cfg))
    try:
        assert [e["name"] for e in _get_json(port, "/api/secrets")] == ["LOGIN_PASSWORD"]
        # And it cannot be written through the endpoint either.
        code, _ = _post(port, "/api/secrets", {"name": "PATH", "value": "x"})
        assert code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_reserved_operator_secret_name_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config that declares a reserved operator-credential name (the AI key, the Claude Code OAuth
    token, or the Git credential) as a scenario secret never offers it, and cannot write it — the
    ``_RESERVED_SECRET_NAMES`` guard keeps a scenario secret from aliasing one of those, which would
    otherwise let ``GET /api/secrets`` disclose the real credential's masked preview to any actor, and
    ``POST /api/secrets`` overwrite it (both endpoints share ``declared_secret_names``)."""
    monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reserved = [AI_API_KEY_SECRET, AI_CLAUDE_CODE_TOKEN_SECRET, GIT_CONFIG_TOKEN_SECRET]
    cfg = _project_with_secrets(
        tmp_path, demo_secrets="[LOGIN_PASSWORD, " + ", ".join(reserved) + "]"
    )
    server, port = _serve(_state(tmp_path, cfg))
    try:
        assert [e["name"] for e in _get_json(port, "/api/secrets")] == ["LOGIN_PASSWORD"]
        for name in reserved:
            code, _ = _post(port, "/api/secrets", {"name": name, "value": "x"})
            assert code == 400
        # The real AI key env var was never touched by any of the rejected writes.
        assert "ANTHROPIC_API_KEY" not in os.environ
    finally:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        server.shutdown()
        server.server_close()


def test_post_rejects_an_undeclared_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/secrets refuses a name the bound config does not declare (400) — the endpoint is
    not an arbitrary-environment-variable-write primitive."""
    monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
    monkeypatch.delenv("NOT_DECLARED", raising=False)
    cfg = _project_with_secrets(tmp_path, demo_secrets="[LOGIN_PASSWORD]")
    server, port = _serve(_state(tmp_path, cfg))
    try:
        code, body = _post(port, "/api/secrets", {"name": "NOT_DECLARED", "value": "secret"})
        assert code == 400 and "not a secret declared" in body["error"]
        assert "NOT_DECLARED" not in os.environ  # nothing was written
    finally:
        server.shutdown()
        server.server_close()


def test_set_describe_and_clear_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared name round-trips: unset → set (masked) → clear. Write-once — no endpoint ever
    returns the plaintext. The value lands in the process env under its own name (BE-0032), so a
    spawned run inheriting ``os.environ`` via ``jobs._spawn_env`` resolves ``${secrets.LOGIN_PASSWORD}``."""
    monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # clean start + auto-restore at teardown
    cfg = _project_with_secrets(tmp_path, demo_secrets="[LOGIN_PASSWORD]")
    server, port = _serve(_state(tmp_path, cfg))
    try:
        assert _get_json(port, "/api/secrets") == [
            {"name": "LOGIN_PASSWORD", "set": False, "masked": None}
        ]
        code, body = _post(
            port, "/api/secrets", {"name": "LOGIN_PASSWORD", "value": "hunter2-secret"}
        )
        assert code == 200 and body["set"] is True
        assert body["masked"] == "hunt…cret" and "secret" not in body["masked"]
        assert "value" not in body  # the mutating side never echoes the plaintext back
        # It lands under its own declared name — not the AI key's env var.
        assert os.environ["LOGIN_PASSWORD"] == "hunter2-secret"
        assert "ANTHROPIC_API_KEY" not in os.environ
        assert not (tmp_path / ".env").exists()  # nothing persisted to disk
        # A spawned run inherits it through the same env the store wrote into.
        assert _spawn_env(srv.Job(cmd=["x"]))["LOGIN_PASSWORD"] == "hunter2-secret"
        # GET now reports it set, masked only — never the plaintext.
        assert _get_json(port, "/api/secrets") == [
            {"name": "LOGIN_PASSWORD", "set": True, "masked": "hunt…cret"}
        ]
        # An empty value clears it — both the mask and the env var go away.
        code, body = _post(port, "/api/secrets", {"name": "LOGIN_PASSWORD", "value": ""})
        assert code == 200 and body["set"] is False
        assert _get_json(port, "/api/secrets")[0]["set"] is False
        assert "LOGIN_PASSWORD" not in os.environ
    finally:
        monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
        server.shutdown()
        server.server_close()


def test_value_with_spaces_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike an API key or token, a scenario secret (a login password, say) may legitimately
    contain whitespace — there is no whitespace guard on the value."""
    monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
    cfg = _project_with_secrets(tmp_path, demo_secrets="[LOGIN_PASSWORD]")
    server, port = _serve(_state(tmp_path, cfg))
    try:
        code, body = _post(
            port, "/api/secrets", {"name": "LOGIN_PASSWORD", "value": "pass phrase with spaces"}
        )
        assert code == 200 and body["set"] is True
        assert os.environ["LOGIN_PASSWORD"] == "pass phrase with spaces"
    finally:
        monkeypatch.delenv("LOGIN_PASSWORD", raising=False)
        server.shutdown()
        server.server_close()
