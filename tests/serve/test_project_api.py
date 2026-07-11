"""BE-0225 unit 3: the five `/api/projects…` operations, org-scoped and additive to the existing
single-config endpoints. Driven at the operations layer (the shared logic both transports call) on
the Linux gate — real `LocalProjectRegistry` JSON store and a real local artifact store, no mocks.
The DELETE-verb / role-gate wiring is exercised in the transport tests; here we pin the behavior."""

from __future__ import annotations

from pathlib import Path

from _shared import fake_popen, project, write_run

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.project_registry import LocalProjectRegistry


def _hub_state(tmp_path: Path, *, hosted: bool = False, **kw: object) -> srv.ServeState:
    """A serve state with a `LocalProjectRegistry` wired, the local-hub shape unit 3 targets."""
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        project_registry=reg,
        hosted=hosted,
        **kw,  # type: ignore[arg-type]
    )


def test_register_then_list_and_the_first_project_becomes_active(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)

    payload, status = ops.register_project(
        state, {"name": "checkout", "source": {"kind": "git", "locator": {"repo": "shop"}}}
    )

    assert status == 200
    assert payload["name"] == "checkout"
    # First project in an org with no active project auto-activates, so a non-`default` org gains an
    # active project through the API (unit 2's boot auto-activation only covered the launch config).
    assert payload["active"] is True

    listed, status = ops.list_projects_view(state)
    assert status == 200
    assert [p["name"] for p in listed] == ["checkout"]
    assert listed[0]["active"] is True
    assert listed[0]["source"] == {"kind": "git", "locator": {"repo": "shop"}}


def test_register_is_idempotent_by_name_and_rebinds_the_source(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    ops.register_project(
        state, {"name": "checkout", "source": {"kind": "git", "locator": {"a": 1}}}
    )

    payload, status = ops.register_project(
        state, {"name": "checkout", "source": {"kind": "file", "locator": {"path": "/x.yaml"}}}
    )

    assert status == 200
    assert payload["source"] == {"kind": "file", "locator": {"path": "/x.yaml"}}
    listed, _ = ops.list_projects_view(state)
    assert [p["name"] for p in listed] == ["checkout"]  # not duplicated


def test_register_requires_a_name(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    payload, status = ops.register_project(state, {"source": None})
    assert status == 400
    assert "name" in payload["error"]


def test_register_rejects_a_filesystem_source_when_hosted(tmp_path: Path) -> None:
    # BE-0108: a hosted deployment offers only git + upload; a client-supplied filesystem path is
    # refused server-side, not merely hidden in the UI.
    state = _hub_state(tmp_path, hosted=True)
    _, status = ops.register_project(
        state, {"name": "checkout", "source": {"kind": "file", "locator": {"path": "/etc/x"}}}
    )
    assert status == 403


def test_register_allows_git_and_upload_when_hosted(tmp_path: Path) -> None:
    state = _hub_state(tmp_path, hosted=True)
    _, git = ops.register_project(state, {"name": "a", "source": {"kind": "git", "locator": {}}})
    _, up = ops.register_project(state, {"name": "b", "source": {"kind": "upload", "locator": {}}})
    assert (git, up) == (200, 200)


def test_register_rejects_an_unknown_source_kind(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    _, status = ops.register_project(state, {"name": "a", "source": {"kind": "smtp"}})
    assert status == 400


def test_deregister_retains_the_runs_and_drops_the_label(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    reg = state.project_registry
    assert reg is not None
    ops.register_project(state, {"name": "checkout", "source": None})
    project_id = reg.get(org_id="default", name="checkout").id  # type: ignore[union-attr]
    write_run(state.runs_dir, "20260711-1", ok=True, scenarios=[("alpha", True)])
    reg.tag_run(org_id="default", project_id=project_id, run_id="20260711-1")

    payload, status = ops.deregister_project(state, "checkout")

    assert (status, payload) == (200, {"ok": True})
    assert reg.get(org_id="default", name="checkout") is None
    # The run stays on disk; only its project label is gone.
    assert (state.runs_dir / "20260711-1" / "manifest.json").exists()
    assert reg.run_ids(org_id="default", project_id=project_id) == []


def test_deregister_unknown_project_is_404(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    _, status = ops.deregister_project(state, "nope")
    assert status == 404


def test_project_runs_returns_only_that_projects_runs_newest_first(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    reg = state.project_registry
    assert reg is not None
    ops.register_project(state, {"name": "checkout", "source": None})
    pid = reg.get(org_id="default", name="checkout").id  # type: ignore[union-attr]
    for rid in ("20260711-1", "20260711-2"):
        write_run(state.runs_dir, rid, ok=True, scenarios=[("alpha", True)])
        reg.tag_run(org_id="default", project_id=pid, run_id=rid)
    # A run that belongs to no project must not surface in the project's slice.
    write_run(state.runs_dir, "20260711-3", ok=True, scenarios=[("alpha", True)])

    payload, status = ops.project_runs(state, "checkout")

    assert status == 200
    assert [r["id"] for r in payload] == ["20260711-2", "20260711-1"]


def test_project_runs_unknown_project_is_404(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    _, status = ops.project_runs(state, "nope")
    assert status == 404


def test_run_project_unknown_is_404(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    _, status = ops.run_project(state, "nope", {"target": "demo", "scenario": "smoke.yaml"})
    assert status == 404


def test_run_project_that_is_not_active_is_409(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    ops.register_project(state, {"name": "checkout", "source": None})  # first → active
    ops.register_project(state, {"name": "billing", "source": None})  # second → not active

    _, status = ops.run_project(state, "billing", {"target": "demo", "scenario": "smoke.yaml"})

    # Running a non-active project needs the live rebind that unit 4's switcher owns; unit 3 refuses
    # rather than run the wrong config.
    assert status == 409


def test_run_project_dispatches_and_stamps_the_project_id(tmp_path: Path) -> None:
    state = _hub_state(
        tmp_path,
        popen=fake_popen(["PASS  runs/20260711-9/manifest.json\n"]),  # type: ignore[arg-type]
    )
    reg = state.project_registry
    assert reg is not None
    ops.register_project(state, {"name": "checkout", "source": None})  # first → active
    pid = reg.get(org_id="default", name="checkout").id  # type: ignore[union-attr]

    payload, status = ops.run_project(
        state, "checkout", {"target": "demo", "scenario": "smoke.yaml"}
    )

    assert status == 200 and "jobId" in payload
    job = state.jobs[payload["jobId"]]
    # The active project's id is resolved at enqueue and carried on the job so a remote worker's
    # `_persist_run` stamps the run without needing a registry (unit 2 review carry-over).
    assert job.project_id == pid


def test_start_run_carries_the_active_project_id_onto_the_job(tmp_path: Path) -> None:
    state = _hub_state(
        tmp_path,
        popen=fake_popen(["PASS  runs/20260711-8/manifest.json\n"]),  # type: ignore[arg-type]
    )
    reg = state.project_registry
    assert reg is not None
    ops.register_project(state, {"name": "checkout", "source": None})  # first → active
    pid = reg.get(org_id="default", name="checkout").id  # type: ignore[union-attr]

    payload, status = ops.start_run(state, {"target": "demo", "scenario": "smoke.yaml"})

    assert status == 200
    assert state.jobs[payload["jobId"]].project_id == pid


def test_register_project_rejects_a_name_containing_a_slash(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    _, status = ops.register_project(state, {"name": "a/b", "source": None})
    assert status == 400


def test_a_registry_error_at_enqueue_leaves_the_run_unlabeled(tmp_path: Path) -> None:
    """`_active_project_id` in `dispatch.py` guards the same way as `_persist_run`: a flaky registry
    at enqueue time degrades to an unlabeled run (project_id=None) rather than breaking start_run."""

    class _FlakyRegistry(LocalProjectRegistry):
        def resolve_active(self, *, org_id: str) -> None:  # type: ignore[override]
            raise RuntimeError("registry backend unavailable")

    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        project_registry=_FlakyRegistry(tmp_path / "projects.json"),
        popen=fake_popen(["PASS  runs/20260711-7/manifest.json\n"]),  # type: ignore[arg-type]
    )

    payload, status = ops.start_run(state, {"target": "demo", "scenario": "smoke.yaml"})

    assert status == 200
    # resolve_active raised, so the job is enqueued but unlabeled (project_id=None).
    assert state.jobs[payload["jobId"]].project_id is None
