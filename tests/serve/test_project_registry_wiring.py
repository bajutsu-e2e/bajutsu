"""BE-0225 unit 2 wiring: the local backend assembles a `LocalProjectRegistry`, the launch config
auto-registers as the active project, and a finished run is stamped with that project — the
`runs.project_id` column with a database, the local project→run-ids index without one. Driven
through `_build_state` / `run_job` on the Linux gate (no Simulator), against a real JSON file and a
repository on in-memory SQLite in the gate and, behind the `postgres` marker, against a real Postgres
service in the serve-db.yml lane (BE-0309) — no mocks."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from _shared import fake_popen, project, write_run
from sqlalchemy import Engine

from bajutsu import serve as srv
from bajutsu.serve.operations.config import launch_project_identity, register_launch_project
from bajutsu.serve.project_registry import LocalProjectRegistry, SqlProjectRegistry
from bajutsu.serve.server.db import ProjectRecord, SqlRepository
from bajutsu.serve.server.models import Base


def test_local_backend_wires_a_local_project_registry(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    state = srv._build_state(
        runs_dir=runs,
        config=None,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=1,
        token=None,
    )
    assert isinstance(state.project_registry, LocalProjectRegistry)
    # It persists beside runs_dir, the local stand-in for the projects/runs tables.
    state.project_registry.add(org_id="default", name="checkout", source=None)
    assert (runs.parent / "projects.json").exists()


def test_launch_identity_of_a_file_config() -> None:
    name, source = launch_project_identity(Path("/apps/checkout.yaml"), None)
    assert name == "checkout"
    assert source == {"kind": "file", "locator": {"path": "/apps/checkout.yaml"}}


def test_launch_identity_of_a_git_config() -> None:
    provenance = {
        "host": "github.com",
        "owner": "acme",
        "repo": "shop",
        "ref": "main",
        "sha": "deadbeef",
    }
    name, source = launch_project_identity(Path("/cache/opaque/config.yaml"), provenance)
    assert name == "shop"
    assert source == {"kind": "git", "locator": provenance}


def test_register_launch_project_registers_and_activates(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    state = srv.ServeState(
        runs_dir=tmp_path / "runs", config=Path("/apps/checkout.yaml"), project_registry=reg
    )

    register_launch_project(state)

    active = reg.resolve_active(org_id="default")
    assert active is not None and active.name == "checkout"
    assert active.source == {"kind": "file", "locator": {"path": "/apps/checkout.yaml"}}


def test_register_launch_project_is_a_no_op_without_a_config(tmp_path: Path) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    state = srv.ServeState(runs_dir=tmp_path / "runs", config=None, project_registry=reg)

    register_launch_project(state)

    assert reg.list_projects(org_id="default") == []


def test_finished_run_is_tagged_to_the_active_project_locally(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260711-1", ok=True, scenarios=[("alpha", True)])
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    p = reg.add(org_id="default", name="checkout", source=None)
    reg.set_active(org_id="default", name="checkout")
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        project_registry=reg,
        popen=fake_popen(["PASS  runs/20260711-1/manifest.json\n"]),
    )
    job = state.register(srv.Job(cmd=["x"]))

    srv.run_job(state, job)

    assert reg.run_ids(org_id="default", project_id=p.id) == ["20260711-1"]


def test_register_launch_project_never_crashes_boot_on_a_registry_error(tmp_path: Path) -> None:
    """Auto-registration is a boot-time convenience (its docstring: "for free", "a no-op when no
    registry is wired") — it must not be able to crash `serve()` startup. A registry whose `add`
    raises (a read-only `runs_dir.parent`, a DB error) is logged and skipped, matching the sibling
    boot seam `restore_persisted_provider_settings`, not propagated out of the boot path."""

    class _FlakyRegistry(LocalProjectRegistry):
        def add(self, *, org_id: str, name: str, source: dict[str, object] | None) -> ProjectRecord:
            raise RuntimeError("read-only runs dir")

    reg = _FlakyRegistry(tmp_path / "projects.json")
    state = srv.ServeState(
        runs_dir=tmp_path / "runs", config=Path("/apps/checkout.yaml"), project_registry=reg
    )

    register_launch_project(state)  # must not raise


def test_a_registry_error_never_strands_run_finalization(tmp_path: Path) -> None:
    """`_persist_run`'s contract (its docstring): any error is caught and logged, never stranding the
    live-log stream that `run_job` closes right after it. Resolving the active project is a registry
    call — `SqlProjectRegistry` reaches the database — so a flaky backend must be caught there too, not
    only around the persistence write. A registry whose `resolve_active` raises must let the run
    finish (unlabeled), not propagate out of `run_job`."""
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260711-3", ok=True, scenarios=[("alpha", True)])

    class _FlakyRegistry(LocalProjectRegistry):
        def resolve_active(self, *, org_id: str) -> None:
            raise RuntimeError("registry backend unavailable")

    reg = _FlakyRegistry(tmp_path / "projects.json")
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        project_registry=reg,
        popen=fake_popen(["PASS  runs/20260711-3/manifest.json\n"]),
    )
    job = state.register(srv.Job(cmd=["x"]))

    srv.run_job(state, job)  # must not raise, despite resolve_active blowing up

    assert job.run_id == "20260711-3"


def test_finished_run_is_stamped_with_the_active_project_id_in_the_db(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260711-2", ok=True, scenarios=[("alpha", True)])
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="Default")
    reg = SqlProjectRegistry(repo)
    p = reg.add(org_id="default", name="checkout", source=None)
    reg.set_active(org_id="default", name="checkout")
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        repository=repo,
        project_registry=reg,
        popen=fake_popen(["PASS  runs/20260711-2/manifest.json\n"]),
    )
    job = state.register(srv.Job(cmd=["x"]))

    srv.run_job(state, job)

    rec = repo.get_run("20260711-2")
    assert rec is not None and rec.project_id == p.id
    # The DB partition is the column itself: list_runs(project_id=...) finds the run.
    assert [r.id for r in repo.list_runs(org_id="default", project_id=p.id)] == ["20260711-2"]


def test_launch_registration_covers_every_live_org(
    tmp_path: Path, serve_engine: Callable[..., Engine]
) -> None:
    """BE-0393 unit 4: the launch configuration is what a member of *any* org reaches when nothing
    else is bound, so a multi-tenant deployment registers it for each org rather than for `default`
    alone — which left every other org staring at an empty hub."""
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="Default")
    repo.ensure_org("acme", slug="acme", name="Acme")
    reg = SqlProjectRegistry(repo)
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        config=Path("/apps/checkout.yaml"),
        project_registry=reg,
        repository=repo,
    )

    register_launch_project(state)

    for org in ("default", "acme"):
        active = reg.resolve_active(org_id=org)
        assert active is not None and active.name == "checkout"


def test_launch_registration_without_a_database_stays_the_single_default_org(
    tmp_path: Path,
) -> None:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    state = srv.ServeState(
        runs_dir=tmp_path / "runs", config=Path("/apps/checkout.yaml"), project_registry=reg
    )

    register_launch_project(state)

    assert [p.name for p in reg.list_projects(org_id="default")] == ["checkout"]


def test_launch_registration_never_overwrites_what_an_org_already_remembers(
    tmp_path: Path, serve_engine: Callable[..., Engine]
) -> None:
    """BE-0393: the launch configuration is the fallback, below the configuration an org last bound.
    Marking it active on every boot would undo the persistence unit 4 exists for — a restart would
    hand every org back the launch config it had deliberately switched away from."""
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="Default")
    reg = SqlProjectRegistry(repo)
    reg.add(org_id="default", name="shop", source={"kind": "git", "locator": {"repo": "shop"}})
    reg.set_active(org_id="default", name="shop")
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        config=Path("/apps/checkout.yaml"),
        project_registry=reg,
        repository=repo,
    )

    register_launch_project(state)

    active = reg.resolve_active(org_id="default")
    assert active is not None and active.name == "shop"
    # Registered all the same, so the org can switch to it from the hub.
    assert sorted(p.name for p in reg.list_projects(org_id="default")) == ["checkout", "shop"]


def test_launch_registration_leaves_a_locally_remembered_project_alone(tmp_path: Path) -> None:
    # The JSON-backed registry persists the active project too, so the same boot clobber applied
    # there — a `serve --config X` restart used to undo a local operator's `project use`.
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    reg.add(org_id="default", name="shop", source=None)
    reg.set_active(org_id="default", name="shop")
    state = srv.ServeState(
        runs_dir=tmp_path / "runs", config=Path("/apps/checkout.yaml"), project_registry=reg
    )

    register_launch_project(state)

    active = reg.resolve_active(org_id="default")
    assert active is not None and active.name == "shop"


def test_launch_registration_never_rebinds_an_entry_it_finds(tmp_path: Path) -> None:
    """The launch config's derived name can collide with one a member's bind already registered — two
    different `bajutsu.config.yaml` files both derive `bajutsu.config` — and `add` rebinds an
    existing name's source. Boot must leave that entry's source alone, or the org's memory is
    defeated through the record instead of the active pointer (BE-0393)."""
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    remembered: dict[str, object] = {"kind": "file", "locator": {"path": "/members/own.yaml"}}
    reg.add(org_id="default", name="checkout", source=remembered)
    state = srv.ServeState(
        runs_dir=tmp_path / "runs", config=Path("/apps/checkout.yaml"), project_registry=reg
    )

    register_launch_project(state)

    entry = reg.get(org_id="default", name="checkout")
    assert entry is not None and entry.source == remembered
