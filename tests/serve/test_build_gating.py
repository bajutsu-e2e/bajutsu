"""BE-0121: a Git config bound at runtime via the API is untrusted, so its `build:` command runs
only behind the `--allow-remote-build` opt-in — mirroring the `build = None` guard uploaded bundles
already get (BE-0090). A local or startup-bound config stays operator-trusted."""

from __future__ import annotations

from pathlib import Path

from _shared import _post, _serve, project

from bajutsu import serve as srv
from bajutsu.serve.operations.dispatch import _governed_build


def _state(tmp_path: Path) -> srv.ServeState:
    return srv.ServeState(runs_dir=tmp_path / "runs", root=tmp_path, cwd=tmp_path)


def test_governed_build_keeps_a_trusted_config_build(tmp_path: Path) -> None:
    # A local/startup config (neither an upload nor an API-bound Git config) is operator-trusted:
    # its build command is left intact.
    assert _governed_build(_state(tmp_path), "make build") == "make build"


def test_governed_build_nulls_api_bound_git_build(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.git_config_from_api = True
    assert _governed_build(state, "make build") is None


def test_governed_build_runs_api_bound_git_build_with_opt_in(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.git_config_from_api = True
    state.allow_remote_build = True
    assert _governed_build(state, "make build") == "make build"


def test_api_bound_git_config_is_marked_untrusted(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Binding a Git config through /api/config marks it untrusted; binding a local config after
    # clears the flag, so the trust boundary tracks whichever source is currently active.
    import bajutsu.serve.operations.config as ops
    from bajutsu.config_source import Materialized

    _, cfg, runs = project(tmp_path)
    checkout = tmp_path / "gitsrc"
    checkout.mkdir()
    git_cfg = checkout / "bajutsu.config.yaml"
    git_cfg.write_text("targets:\n  fromgit: { bundleId: com.example.fromgit }\n", encoding="utf-8")
    monkeypatch.setattr(
        ops, "materialize", lambda spec, **kw: Materialized(git_cfg, checkout, "sha")
    )
    state = srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, _ = _post(port, "/api/config", {"git": "github:acme/repo@main"})
        assert status == 200
        assert state.git_config_from_api is True
        # A local config bound afterwards is operator-trusted again.
        status, _ = _post(port, "/api/config", {"path": str(cfg)})
        assert status == 200
        assert state.git_config_from_api is False
    finally:
        server.shutdown()
        server.server_close()
