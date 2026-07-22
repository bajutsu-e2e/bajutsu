"""BE-0225 unit 3: the five `/api/projects…` operations, org-scoped and additive to the existing
single-config endpoints. Driven at the operations layer (the shared logic both transports call) on
the Linux gate — real `LocalProjectRegistry` JSON store and a real local artifact store, no mocks.
The DELETE-verb / role-gate wiring is exercised in the transport tests; here we pin the behavior."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from _shared import FakeObjectStore, fake_popen, project, write_run

from bajutsu import serve as srv
from bajutsu.config_source import source_from_config
from bajutsu.serve import operations as ops
from bajutsu.serve.operations.upload import activate_uploaded_project
from bajutsu.serve.project_registry import LocalProjectRegistry


def _bundle_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bajutsu.config.yaml", "targets: {}\n")
    return buf.getvalue()


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


def test_register_accepts_a_source_spec_string(tmp_path: Path) -> None:
    # The Web UI Add form (BE-0275) sends a single `sourceSpec` string — a Git spec or a local path,
    # exactly what `bajutsu project add --config` takes — instead of a pre-built record. The server
    # normalizes it through the one canonical parser, so a Git spec lands as a `git` source and a
    # bare path as a `file` source; the browser never re-implements the spec grammar.
    state = _hub_state(tmp_path)

    spec = "github:acme/shop@v1:cfg/bajutsu.config.yaml"
    payload, status = ops.register_project(state, {"name": "shop", "sourceSpec": spec})
    assert status == 200
    assert payload["source"] == source_from_config(spec)
    assert payload["source"]["kind"] == "git"

    payload, status = ops.register_project(
        state, {"name": "local", "sourceSpec": "/srv/app/bajutsu.config.yaml"}
    )
    assert status == 200
    assert payload["source"] == {
        "kind": "file",
        "locator": {"path": "/srv/app/bajutsu.config.yaml"},
    }


def test_source_spec_is_screened_by_the_allowlist_when_hosted(tmp_path: Path) -> None:
    # A local-path `sourceSpec` normalizes to a `file` source, which a hosted server refuses
    # (BE-0108) — the same screening a hand-built `file` record hits, so both entry points share one
    # allowlist path rather than the string form slipping past it.
    state = _hub_state(tmp_path, hosted=True)
    _, status = ops.register_project(state, {"name": "local", "sourceSpec": "/etc/x.yaml"})
    assert status == 403


def test_register_treats_a_whitespace_only_source_spec_as_absent(tmp_path: Path) -> None:
    # A whitespace-only `sourceSpec` (e.g. a direct API call bypassing the Add form's own `.trim()`)
    # must be the same no-op as an omitted source, not a `file` source whose path is blank spaces.
    state = _hub_state(tmp_path)
    payload, status = ops.register_project(state, {"name": "blank", "sourceSpec": "   "})
    assert status == 200
    assert payload["source"] is None


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


def _file_config(path: Path, target: str = "demo") -> Path:
    """A minimal, loadable config file so `bind_config` accepts it when a project is activated."""
    path.write_text(
        f"defaults: {{ backend: [ios] }}\ntargets:\n  {target}: {{ bundleId: com.example.{target} }}\n",
        encoding="utf-8",
    )
    return path


def test_activate_switches_the_active_project_and_rebinds_the_config(tmp_path: Path) -> None:
    # A file source can only bind when confined to the browse root, so the hub's root is tmp_path.
    state = _hub_state(tmp_path, root=tmp_path)
    first = _file_config(tmp_path / "first.config.yaml", "first")
    second = _file_config(tmp_path / "second.config.yaml", "second")
    ops.register_project(
        state, {"name": "first", "source": {"kind": "file", "locator": {"path": str(first)}}}
    )
    ops.register_project(
        state, {"name": "second", "source": {"kind": "file", "locator": {"path": str(second)}}}
    )
    reg = state.project_registry
    assert reg is not None

    payload, status = ops.activate_project(state, "second")

    assert status == 200
    assert payload["active"] is True
    # The active project flips *and* the whole UI now runs against the switched-to config, without a
    # restart — the hub behavior unit 4 owns (unit 3 refused a non-active run with a 409).
    assert reg.resolve_active(org_id="default").name == "second"  # type: ignore[union-attr]
    assert Path(state.config).resolve() == second.resolve()


def test_run_project_after_activating_it_dispatches(tmp_path: Path) -> None:
    state = _hub_state(
        tmp_path,
        root=tmp_path,
        popen=fake_popen(["PASS  runs/20260711-5/manifest.json\n"]),  # type: ignore[arg-type]
    )
    first = _file_config(tmp_path / "first.config.yaml", "first")
    second = _file_config(tmp_path / "second.config.yaml", "second")
    ops.register_project(
        state, {"name": "first", "source": {"kind": "file", "locator": {"path": str(first)}}}
    )
    ops.register_project(
        state, {"name": "second", "source": {"kind": "file", "locator": {"path": str(second)}}}
    )

    # Before switching, running the non-active project is refused (unit 3's 409).
    _, before = ops.run_project(state, "second", {"target": "demo", "scenario": "smoke.yaml"})
    assert before == 409

    ops.activate_project(state, "second")
    payload, status = ops.run_project(state, "second", {"target": "demo", "scenario": "smoke.yaml"})

    assert status == 200 and "jobId" in payload


def test_activate_unknown_project_is_404(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    _, status = ops.activate_project(state, "nope")
    assert status == 404


def test_activate_a_project_with_no_source_is_400(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    ops.register_project(state, {"name": "checkout", "source": None})
    _, status = ops.activate_project(state, "checkout")
    # A rename-only registration has no config source to bind, so it cannot become the live target.
    assert status == 400


def test_activate_an_upload_project_is_409(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    ops.register_project(state, {"name": "bundle", "source": {"kind": "upload", "locator": {}}})
    _, status = ops.activate_project(state, "bundle")
    # An uploaded bundle has no local checkout to re-materialize, so switching to it is refused with a
    # 409 rather than silently binding nothing — the operator re-uploads it instead.
    assert status == 409


def test_activate_an_upload_project_fetches_from_the_object_store(tmp_path: Path) -> None:
    # BE-0243: when the durable bytes are configured and present, activating an upload-kind project
    # no longer 409s — it fetches, extracts, and binds them like any other source.
    blob = _bundle_zip()
    sha256 = hashlib.sha256(blob).hexdigest()
    store = FakeObjectStore({f"uploads/{sha256}.zip": blob})
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    ops.register_project(
        state,
        {
            "name": "bundle",
            "source": {
                "kind": "upload",
                "filename": "suite.zip",
                "sha256": sha256,
                "size": len(blob),
            },
        },
    )
    payload, status = ops.activate_project(state, "bundle")
    assert status == 200 and payload["active"] is True
    assert state.config is not None and state.config.name == "bajutsu.config.yaml"


def test_activate_an_upload_project_is_still_409_when_the_key_is_absent(tmp_path: Path) -> None:
    # A store is configured, but this bundle's bytes were never persisted there (or the key was
    # evicted) — falls back to the original 409 rather than a different error.
    store = FakeObjectStore()
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    ops.register_project(
        state, {"name": "bundle", "source": {"kind": "upload", "sha256": "a" * 64, "size": 1}}
    )
    _, status = ops.activate_project(state, "bundle")
    assert status == 409


def test_activate_an_upload_project_store_fetch_failure_is_a_400_not_a_409(tmp_path: Path) -> None:
    # A transient store error (not "key absent") on the get_bytes fetch must be reported as a real
    # error, not silently folded into the None/409 "nothing to restore from" fallback.
    store = FakeObjectStore()
    store.fail_with = ConnectionError("bucket unreachable")
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    ops.register_project(
        state, {"name": "bundle", "source": {"kind": "upload", "sha256": "a" * 64, "size": 1}}
    )
    payload, status = ops.activate_project(state, "bundle")
    assert status == 400 and "could not fetch" in payload["error"]


def test_activate_an_upload_project_cache_hit_never_touches_the_store(tmp_path: Path) -> None:
    # A replica that already extracted this sha256 (an earlier upload/activation) reuses it without
    # asking the object store at all — proven here by a store that raises if ever called.
    blob = _bundle_zip()
    sha256 = hashlib.sha256(blob).hexdigest()
    uploads_dir = tmp_path / "uploads"
    (uploads_dir / sha256).mkdir(parents=True)
    (uploads_dir / sha256 / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")

    class _PoisonedStore(FakeObjectStore):
        def get_bytes(self, key: str) -> bytes | None:
            raise AssertionError(f"should not touch the object store for a local cache hit: {key}")

    state = _hub_state(tmp_path, object_store=_PoisonedStore(), uploads_dir=uploads_dir)
    ops.register_project(
        state, {"name": "bundle", "source": {"kind": "upload", "sha256": sha256, "size": len(blob)}}
    )
    _, status = ops.activate_project(state, "bundle")
    assert status == 200


def test_activate_uploaded_project_does_not_cross_org_cache_boundaries(tmp_path: Path) -> None:
    # Regression for the org-scoping fix (BE-0243): org B claiming org A's sha256 in its own project
    # record must not cache-hit into org A's already-extracted local tree just because the two
    # happen to share the same serve process's uploads_dir.
    blob = _bundle_zip()
    sha256 = hashlib.sha256(blob).hexdigest()
    uploads_dir = tmp_path / "uploads"
    org_a_dir = uploads_dir / "orgA" / sha256
    org_a_dir.mkdir(parents=True)
    (org_a_dir / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")

    state = _hub_state(tmp_path, object_store=FakeObjectStore(), uploads_dir=uploads_dir)
    source = {"kind": "upload", "filename": "x.zip", "sha256": sha256, "size": len(blob)}
    # org B has no matching key in the (shared) store and no cache entry of its own — must be None
    # (falls back to the 409), never a bind into org A's tree.
    assert activate_uploaded_project(state, source, org="orgB") is None
    # org A's own activation, by contrast, cache-hits its own entry.
    result = activate_uploaded_project(state, source, org="orgA")
    assert result is not None and result[1] == 200


def test_activate_an_upload_project_with_a_corrupt_fetched_bundle_is_400(tmp_path: Path) -> None:
    # The bytes exist at the store key (unlike the absent-key 409 case above) but are not a valid
    # bundle — a real error, distinct from "nothing to restore from", so it must not fall back to
    # the 409 a caller would otherwise read as "no durable copy exists at all".
    sha256 = "b" * 64
    store = FakeObjectStore({f"uploads/{sha256}.zip": b"not a valid zip"})
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    ops.register_project(
        state, {"name": "bundle", "source": {"kind": "upload", "sha256": sha256, "size": 1}}
    )
    payload, status = ops.activate_project(state, "bundle")
    assert status == 400 and "invalid bundle" in payload["error"]


def test_activate_uploaded_project_rejects_a_malformed_sha256(tmp_path: Path) -> None:
    # A registered project's source is client-suppliable (BE-0225); a sha256 that isn't a well-formed
    # hex digest must never reach a filesystem path or object-store key — treated as nothing to
    # restore from (falls back to the 409), the same as a missing sha256.
    store = FakeObjectStore({"uploads/../../../etc.zip": b"whatever"})
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    source = {"kind": "upload", "sha256": "../../../etc", "size": 1}
    assert activate_uploaded_project(state, source, org="default") is None


def test_activate_uploaded_project_org_scoping_holds_at_the_object_store_too(
    tmp_path: Path,
) -> None:
    # The cache-boundary regression above proves the *local* half of the org-scoping fix; this proves
    # the *object-store* half: org A's bytes live at org A's own store key, and org B — with no local
    # cache entry of its own — must not resolve them just because the sha256 matches.
    blob = _bundle_zip()
    sha256 = hashlib.sha256(blob).hexdigest()
    store = FakeObjectStore({f"orgA/uploads/{sha256}.zip": blob})
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    source = {"kind": "upload", "filename": "x.zip", "sha256": sha256, "size": len(blob)}
    assert activate_uploaded_project(state, source, org="orgB") is None
    result = activate_uploaded_project(state, source, org="orgA")
    assert result is not None and result[1] == 200


def _scenarios_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("scenarios/smoke.yaml", "- name: a\n  steps: []\n")
    return buf.getvalue()


def _composed_config() -> bytes:
    return (
        b"defaults: { backend: [ios] }\n"
        b"targets:\n  demo: { bundleId: com.example.demo, scenarios: ./scenarios }\n"
    )


def test_activate_a_composed_upload_project_fetches_and_composes_from_the_object_store(
    tmp_path: Path,
) -> None:
    # BE-0268: a project bound to a triple of artifact shas (not one bundle sha) composes them into
    # a tree the same way `activate_uploaded_project`'s legacy path materializes one.
    config_blob = _composed_config()
    scenarios_blob = _scenarios_zip()
    config_sha = hashlib.sha256(config_blob).hexdigest()
    scenarios_sha = hashlib.sha256(scenarios_blob).hexdigest()
    store = FakeObjectStore(
        {
            f"uploads/config/{config_sha}": config_blob,
            f"uploads/scenarios/{scenarios_sha}": scenarios_blob,
        }
    )
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    ops.register_project(
        state,
        {
            "name": "composed",
            "source": {
                "kind": "upload",
                "filename": "suite",
                "artifacts": {"config": config_sha, "scenarios": scenarios_sha},
                "size": len(config_blob),
            },
        },
    )
    payload, status = ops.activate_project(state, "composed")
    assert status == 200 and payload["active"] is True
    assert state.config is not None and state.config.name == "bajutsu.config.yaml"


def test_activate_a_composed_upload_project_is_409_when_no_object_store_configured(
    tmp_path: Path,
) -> None:
    # No object store at all — nothing to restore from, same fallback as the legacy single-sha path.
    state = _hub_state(tmp_path, uploads_dir=tmp_path / "uploads")
    ops.register_project(
        state,
        {
            "name": "composed",
            "source": {"kind": "upload", "artifacts": {"config": "a" * 64}, "size": 1},
        },
    )
    _, status = ops.activate_project(state, "composed")
    assert status == 409


def test_activate_a_composed_upload_project_is_409_when_config_sha_is_malformed(
    tmp_path: Path,
) -> None:
    store = FakeObjectStore()
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    ops.register_project(
        state,
        {
            "name": "composed",
            "source": {"kind": "upload", "artifacts": {"config": "not-hex"}, "size": 1},
        },
    )
    _, status = ops.activate_project(state, "composed")
    assert status == 409


def test_activate_a_composed_upload_project_is_404_when_config_bytes_are_absent(
    tmp_path: Path,
) -> None:
    # A well-formed config sha that isn't actually stored anywhere is a real "not found", distinct
    # from the "nothing to restore from at all" 409 fallback above.
    store = FakeObjectStore()  # nothing stored
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    ops.register_project(
        state,
        {
            "name": "composed",
            "source": {"kind": "upload", "artifacts": {"config": "a" * 64}, "size": 1},
        },
    )
    payload, status = ops.activate_project(state, "composed")
    assert status == 404 and "not available" in payload["error"]


def test_activate_a_composed_upload_project_rejects_an_invalid_leg_sha(tmp_path: Path) -> None:
    store = FakeObjectStore()
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    source = {
        "kind": "upload",
        "artifacts": {"config": "a" * 64, "scenarios": "../../../etc"},
        "size": 1,
    }
    result = activate_uploaded_project(state, source, org="default")
    assert result is not None
    payload, status = result
    assert status == 400 and "invalid scenarios" in payload["error"]


def test_activate_a_composed_upload_project_is_400_when_coherence_fails(tmp_path: Path) -> None:
    # The config needs a scenarios artifact but the triple doesn't supply one — a real error, not a
    # silent partial bind.
    config_blob = _composed_config()
    config_sha = hashlib.sha256(config_blob).hexdigest()
    store = FakeObjectStore({f"uploads/config/{config_sha}": config_blob})
    state = _hub_state(tmp_path, object_store=store, uploads_dir=tmp_path / "uploads")
    ops.register_project(
        state,
        {
            "name": "composed",
            "source": {"kind": "upload", "artifacts": {"config": config_sha}, "size": 1},
        },
    )
    payload, status = ops.activate_project(state, "composed")
    assert status == 400 and "invalid composition" in payload["error"]


def test_activate_a_composed_upload_project_cache_hit_never_touches_the_store(
    tmp_path: Path,
) -> None:
    # A replica that already composed this exact triple reuses it without asking the object store
    # at all — proven here by a store that raises if ever called.
    config_blob = _composed_config()
    scenarios_blob = _scenarios_zip()
    config_sha = hashlib.sha256(config_blob).hexdigest()
    scenarios_sha = hashlib.sha256(scenarios_blob).hexdigest()

    class _PoisonedStore(FakeObjectStore):
        def get_bytes(self, key: str) -> bytes | None:
            raise AssertionError(f"should not touch the object store for a cache hit: {key}")

    uploads_dir = tmp_path / "uploads"
    store = FakeObjectStore(
        {
            f"uploads/config/{config_sha}": config_blob,
            f"uploads/scenarios/{scenarios_sha}": scenarios_blob,
        }
    )
    state = _hub_state(tmp_path, object_store=store, uploads_dir=uploads_dir)
    ops.register_project(
        state,
        {
            "name": "composed",
            "source": {
                "kind": "upload",
                "artifacts": {"config": config_sha, "scenarios": scenarios_sha},
                "size": 1,
            },
        },
    )
    # First activation composes and caches; swap in a poisoned store, then activate a second time —
    # the cache-hit path must never call it.
    _, status = ops.activate_project(state, "composed")
    assert status == 200
    state.object_store = _PoisonedStore()
    _, status2 = ops.activate_project(state, "composed")
    assert status2 == 200


def test_activate_a_malformed_git_source_is_400(tmp_path: Path) -> None:
    state = _hub_state(tmp_path)
    # A git locator missing its host cannot rebuild a spec; refuse cleanly rather than 500.
    ops.register_project(
        state, {"name": "gitp", "source": {"kind": "git", "locator": {"owner": "a", "repo": "b"}}}
    )
    _, status = ops.activate_project(state, "gitp")
    assert status == 400


def test_activate_when_no_hub_is_configured_is_400(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    _, status = ops.activate_project(state, "anything")
    assert status == 400


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
