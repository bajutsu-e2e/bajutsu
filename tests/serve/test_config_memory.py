"""BE-0393 unit 3: every bind records the acting org's project, so the configuration an org last
bound is remembered instead of being lost with the process.

Driven at the operations layer against a real `LocalProjectRegistry` JSON store, no mocks — the same
shape `test_project_api.py` uses. What each test pins is *what the org remembers after the bind*: the
project's name, its stored source, and that it is the active one.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from _shared import fake_popen, project

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.operations.config import git_project_name
from bajutsu.serve.project_registry import LocalProjectRegistry, ProjectRegistry
from bajutsu.serve.server.db import ProjectRecord

_CONFIG = "defaults: { backend: [ios] }\ntargets:\n  demo: { bundleId: com.example.demo }\n"


def _hub_state(
    tmp_path: Path, registry: ProjectRegistry | None = None, **kw: object
) -> srv.ServeState:
    reg = registry or LocalProjectRegistry(tmp_path / "projects.json")
    scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        root=tmp_path,
        uploads_dir=tmp_path / "uploads",
        project_registry=reg,
        popen=fake_popen(["PASS  runs/up-1/manifest.json\n"]),
        **kw,  # type: ignore[arg-type]
    )


def _remembered(state: srv.ServeState) -> tuple[str, object]:
    """The name and source of the `default` org's remembered configuration."""
    registry = state.project_registry
    assert registry is not None
    active = registry.resolve_active(org_id="default")
    assert active is not None
    return active.name, active.source


def _bundle_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bajutsu.config.yaml", _CONFIG)
    return buf.getvalue()


def test_a_file_browser_bind_is_remembered_as_the_orgs_project(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    picked = tmp_path / "checkout.yaml"
    picked.write_text(_CONFIG, encoding="utf-8")

    _, status = ops.bind_config(state, str(picked))

    assert status == 200
    assert _remembered(state) == ("checkout", {"kind": "file", "locator": {"path": str(picked)}})


def test_a_git_bind_is_remembered_by_repository_and_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The name carries the in-repo config path, so two configs in one repository are two projects —
    # what the launch registration cannot do, since its provenance stamp has no path.
    state = _hub_state(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "web.yaml").write_text(_CONFIG, encoding="utf-8")

    class _Mat:
        root = checkout
        config_path = checkout / "web.yaml"
        sha = "deadbeef"

    import bajutsu.serve.operations.config as config_ops

    monkeypatch.setattr(config_ops, "materialize", lambda spec: _Mat())

    _, status = ops.bind_git_config(state, "github:acme/shop@main:web.yaml")

    assert status == 200
    name, source = _remembered(state)
    assert name == "shop/web.yaml"
    # The ref the member asked for, not the commit it resolved to: restoring the org later should
    # follow the branch they chose.
    assert source == {
        "kind": "git",
        "locator": {
            "host": "github.com",
            "owner": "acme",
            "repo": "shop",
            "ref": "main",
            "path": "web.yaml",
        },
    }


def test_git_project_name_falls_back_to_the_repository_without_a_path() -> None:
    assert git_project_name("shop", None) == "shop"
    assert git_project_name("shop", "apps/web.yaml") == "shop/apps/web.yaml"


def test_an_uploaded_bundle_is_remembered_by_its_file_name(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    blob = _bundle_zip()
    zip_path = tmp_path / "suite.zip"
    zip_path.write_bytes(blob)
    sha256 = hashlib.sha256(blob).hexdigest()

    _, status = ops.bind_upload_config(state, zip_path, "suite.zip", sha256=sha256)

    assert status == 200
    assert _remembered(state) == (
        "suite.zip",
        {"kind": "upload", "filename": "suite.zip", "sha256": sha256, "size": len(blob)},
    )


def test_a_composed_bind_is_remembered_with_its_per_leg_shas(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    src = tmp_path / "config.blob"
    src.write_bytes(_CONFIG.encode())
    config_sha = hashlib.sha256(_CONFIG.encode()).hexdigest()
    ops.bind_artifact(state, "config", src, sha256=config_sha)

    _, status = ops.bind_composition(state, {"config": config_sha, "filename": "matrix.zip"})

    assert status == 200
    name, source = _remembered(state)
    assert name == "matrix.zip"
    assert source == {
        "kind": "upload",
        "artifacts": {"config": config_sha},
        "filename": "matrix.zip",
    }


def test_switching_projects_does_not_register_a_second_one(tmp_path: Path) -> None:
    # `activate_project` rebinds through the same binders, but records the org's memory itself under
    # the project's own name — the binder must not add a second entry under its derived name.
    state = _hub_state(tmp_path)
    picked = tmp_path / "checkout.config.yaml"
    picked.write_text(_CONFIG, encoding="utf-8")
    ops.register_project(
        state, {"name": "staging", "source": {"kind": "file", "locator": {"path": str(picked)}}}
    )
    registry = state.project_registry
    assert registry is not None

    _, status = ops.activate_project(state, "staging")

    assert status == 200
    assert [p.name for p in registry.list_projects(org_id="default")] == ["staging"]


def test_a_registry_failure_never_fails_the_bind(tmp_path: Path) -> None:
    # Recording the memory is a convenience over the bind; a read-only store must not turn a
    # successful bind into an error the member cannot act on.
    class _FlakyRegistry(LocalProjectRegistry):
        def add(self, *, org_id: str, name: str, source: dict[str, object] | None) -> ProjectRecord:
            raise RuntimeError("read-only runs dir")

    state = _hub_state(tmp_path, registry=_FlakyRegistry(tmp_path / "projects.json"))
    picked = tmp_path / "checkout.config.yaml"
    picked.write_text(_CONFIG, encoding="utf-8")

    _, status = ops.bind_config(state, str(picked))

    assert status == 200
    assert state.config is not None and state.config.resolve() == picked.resolve()
