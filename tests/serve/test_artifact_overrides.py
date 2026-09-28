"""Tests for per-job artifact overrides on a `run` request (BE-0431).

A `run` may name an already-stored `binary` and/or `scenarios` artifact by sha256, and only that job
resolves against them: the org's active config binding, and every other job reading it, stays as it
was. These cases cover the control-plane half — the existence gate, the scenario lookup against the
override's own listing, the topology refusals, and what the lease signs — over a recording executor
and an in-memory object store, so no worker, network, or device is involved.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from conftest import FakeObjectStore

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.operations.composition import CompositionError, place_overrides
from bajutsu.serve.operations.upload import artifact_presence
from bajutsu.serve.upload_artifacts import ArtifactOverrides, artifact_store_key

_CONFIG = (
    "defaults: { backend: [ios] }\n"
    "targets:\n"
    "  demo: { bundleId: com.example.demo, scenarios: ./scenarios, appPath: ./build/Demo.app }\n"
    "  web: { platform: web, baseUrl: 'http://localhost', scenarios: ./web-scenarios }\n"
)
_SCENARIO = "- name: a\n  steps: []\n"


def _digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


class _Recorder:
    """A non-local executor: records what was dispatched instead of running it, like a queue."""

    def __init__(self) -> None:
        self.jobs: list[srv.Job] = []

    def dispatch(self, state: srv.ServeState, job: srv.Job) -> None:
        self.jobs.append(job)


def _hosted(
    tmp_path: Path, *, local: bool = False, config: str = _CONFIG
) -> tuple[srv.ServeState, FakeObjectStore]:
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "bound.yaml").write_text(_SCENARIO, encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(config, encoding="utf-8")
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        config=cfg,
        cwd=tmp_path,
        uploads_dir=tmp_path / "uploads",
    )
    if not local:
        state.executor = _Recorder()
        # The lease worker that places overrides reads its jobs from the database.
        state.repository = MagicMock()
    store = FakeObjectStore()
    state.object_store = store
    return state, store


def _store(store: FakeObjectStore, kind: Any, blob: bytes) -> str:
    sha = _digest(blob)
    store.objects[artifact_store_key("", "default", kind, sha)] = blob
    return sha


def _dispatched(state: srv.ServeState) -> srv.Job:
    assert isinstance(state.executor, _Recorder)
    return state.executor.jobs[-1]


# --- the three-state presence probe -----------------------------------------------------------


def test_presence_is_true_for_a_stored_artifact(tmp_path: Path) -> None:
    state, store = _hosted(tmp_path)
    sha = _store(store, "binary", b"bin")
    assert artifact_presence(state, "default", "binary", sha) is True


def test_presence_is_false_for_a_confirmed_miss(tmp_path: Path) -> None:
    state, _ = _hosted(tmp_path)
    assert artifact_presence(state, "default", "binary", "b" * 64) is False


def test_presence_on_an_unreadable_local_cache_is_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A permission error on the cache is not proof the artifact was never uploaded.
    state, _ = _hosted(tmp_path)
    state.object_store = None
    cache = tmp_path / "uploads" / "artifacts" / "binary"
    cache.mkdir(parents=True)

    def denied(self: Path) -> Any:
        raise PermissionError(self)

    monkeypatch.setattr(Path, "iterdir", denied)
    assert artifact_presence(state, "default", "binary", "b" * 64) is None


def test_presence_without_a_local_cache_is_a_confirmed_miss(tmp_path: Path) -> None:
    state, _ = _hosted(tmp_path)
    state.object_store = None
    assert artifact_presence(state, "default", "binary", "b" * 64) is False


def test_presence_is_unconfirmed_when_the_store_errors(tmp_path: Path) -> None:
    state, store = _hosted(tmp_path)
    store.fail_with = RuntimeError("503 slow down")
    assert artifact_presence(state, "default", "binary", "b" * 64) is None


def test_the_exists_endpoint_keeps_its_two_state_contract_on_a_store_error(tmp_path: Path) -> None:
    # Dedup callers read either a miss or an unconfirmed check as "upload it again", so the HTTP
    # answer stays two-state even though the probe underneath now distinguishes them.
    state, store = _hosted(tmp_path)
    store.fail_with = RuntimeError("503 slow down")
    assert ops.artifact_exists(state, "binary", "b" * 64) == ({"exists": False}, 200)


# --- the run request's override fields --------------------------------------------------------


def _run(state: srv.ServeState, **body: Any) -> tuple[Any, int]:
    return ops.start_run(state, {"scenario": "bound.yaml", "target": "demo", **body})


def test_a_request_naming_neither_field_dispatches_exactly_as_before(tmp_path: Path) -> None:
    state, _ = _hosted(tmp_path)
    _resp, code = _run(state)
    assert code == 200
    job = _dispatched(state)
    assert job.overrides is None
    # The bound scenario resolves on disk, so nothing ships as materials — today's local shape.
    assert job.materials == {}
    assert str(tmp_path / "scenarios" / "bound.yaml") in job.cmd


def test_a_single_process_serve_refuses_either_field(tmp_path: Path) -> None:
    # A LocalExecutor runs the job in the operator's own project directory, which no override may
    # overwrite; the refusal leaves their appPath and scenarios untouched.
    state, store = _hosted(tmp_path, local=True)
    sha = _store(store, "binary", b"bin")
    resp, code = _run(state, binaryArtifact=sha)
    assert code == 400
    assert "hosted" in resp["error"]
    assert not state.jobs
    resp, code = _run(state, scenariosArtifact=sha)
    assert code == 400


def test_an_executor_without_the_lease_worker_refuses_either_field(tmp_path: Path) -> None:
    # A queue executor that runs the job spec directly never places the override, so accepting it
    # would run the bound binary under a manifest naming the override.
    state, store = _hosted(tmp_path)
    state.repository = None
    resp, code = _run(state, binaryArtifact=_store(store, "binary", b"bin"))
    assert code == 400
    assert "hosted" in resp["error"]
    assert not state.executor.jobs  # type: ignore[attr-defined]


def test_a_malformed_digest_is_refused(tmp_path: Path) -> None:
    state, _ = _hosted(tmp_path)
    resp, code = _run(state, binaryArtifact="../../etc/passwd")
    assert code == 400
    assert "binaryArtifact" in resp["error"]


def test_an_artifact_the_org_does_not_hold_is_refused_before_any_job_exists(
    tmp_path: Path,
) -> None:
    state, _ = _hosted(tmp_path)
    resp, code = _run(state, binaryArtifact="b" * 64)
    assert code == 400
    assert "not stored" in resp["error"]
    assert not state.executor.jobs  # type: ignore[attr-defined]


def test_an_unconfirmed_existence_check_is_a_retryable_503_not_a_400(tmp_path: Path) -> None:
    state, store = _hosted(tmp_path)
    store.fail_with = RuntimeError("503 slow down")
    _resp, code = _run(state, binaryArtifact="b" * 64)
    assert code == 503
    assert not state.executor.jobs  # type: ignore[attr-defined]


def test_a_binary_override_keeps_resolving_scenarios_through_the_binding(tmp_path: Path) -> None:
    state, store = _hosted(tmp_path)
    sha = _store(store, "binary", b"bin")
    _resp, code = _run(state, binaryArtifact=sha)
    assert code == 200
    job = _dispatched(state)
    assert job.overrides is not None
    assert (job.overrides.target, job.overrides.binary, job.overrides.scenarios) == (
        "demo",
        sha,
        None,
    )
    # The job is workspace-relative on the worker, so the config travels with it.
    assert job.materials == {"bajutsu.config.yaml": _CONFIG}
    assert str(tmp_path / "scenarios" / "bound.yaml") in job.cmd
    assert job.provenance == {"binaryArtifact": sha}


def test_a_binary_override_on_a_target_with_no_app_path_is_refused(tmp_path: Path) -> None:
    state, store = _hosted(tmp_path)
    (tmp_path / "web-scenarios").mkdir()
    (tmp_path / "web-scenarios" / "bound.yaml").write_text(_SCENARIO, encoding="utf-8")
    sha = _store(store, "binary", b"bin")
    resp, code = _run(state, target="web", binaryArtifact=sha)
    assert code == 400
    assert "appPath" in resp["error"]


def test_a_binary_override_on_an_android_target_is_accepted(tmp_path: Path) -> None:
    # target_build_info only ever names an iOS app_path; the gate must read app_path
    # platform-neutrally (target_batch_info) or every non-iOS target is refused regardless of
    # whether it names an appPath.
    state, store = _hosted(tmp_path, config=_MIXED_CONFIG)
    sha = _store(store, "binary", b"apk")
    resp, code = _run(state, target="android", binaryArtifact=sha)
    assert code == 200, resp
    job = _dispatched(state)
    assert job.overrides is not None
    assert (job.overrides.target, job.overrides.binary) == ("android", sha)


def test_a_scenarios_override_runs_a_scenario_only_it_holds(tmp_path: Path) -> None:
    # The pull-request case: the scenario exists in neither the bound tree nor the org's store.
    state, store = _hosted(tmp_path)
    sha = _store(store, "scenarios", _zip({"scenarios/added.yaml": _SCENARIO.encode()}))
    _resp, code = _run(state, scenario="added.yaml", scenariosArtifact=sha)
    assert code == 200
    job = _dispatched(state)
    assert "scenarios/added.yaml" in job.cmd
    # No scenario text ships inline — the worker places the whole artifact — yet the run is still
    # workspace-relative, so the flag must not have come from `bool(materials)` alone.
    assert job.materials == {"bajutsu.config.yaml": _CONFIG}
    assert "bajutsu.config.yaml" in job.cmd
    assert job.provenance == {"scenariosArtifact": sha}


def test_a_scenario_name_is_reduced_to_its_basename_before_the_lookup(tmp_path: Path) -> None:
    state, store = _hosted(tmp_path)
    sha = _store(store, "scenarios", _zip({"scenarios/added.yaml": _SCENARIO.encode()}))
    _resp, code = _run(state, scenario="../../elsewhere/added.yaml", scenariosArtifact=sha)
    assert code == 200
    assert "scenarios/added.yaml" in _dispatched(state).cmd


def test_a_scenario_absent_from_the_override_is_refused(tmp_path: Path) -> None:
    # `bound.yaml` exists in the bound tree, but the override replaces that directory, so it is not
    # a scenario this job could run.
    state, store = _hosted(tmp_path)
    sha = _store(store, "scenarios", _zip({"scenarios/added.yaml": _SCENARIO.encode()}))
    resp, code = _run(state, scenariosArtifact=sha)
    assert code == 400
    assert "scenario must be an existing .yaml" in resp["error"]


def test_a_single_yaml_scenarios_override_is_refused(tmp_path: Path) -> None:
    state, store = _hosted(tmp_path)
    sha = _store(store, "scenarios", _SCENARIO.encode())
    resp, code = _run(state, scenariosArtifact=sha)
    assert code == 400
    assert "zip" in resp["error"]


def test_both_overrides_record_both_digests_as_provenance(tmp_path: Path) -> None:
    state, store = _hosted(tmp_path)
    binary = _store(store, "binary", b"bin")
    scenarios = _store(store, "scenarios", _zip({"scenarios/added.yaml": _SCENARIO.encode()}))
    _resp, code = _run(
        state, scenario="added.yaml", binaryArtifact=binary, scenariosArtifact=scenarios
    )
    assert code == 200
    assert _dispatched(state).provenance == {
        "binaryArtifact": binary,
        "scenariosArtifact": scenarios,
    }


def test_concurrent_override_jobs_leave_the_deployment_binding_alone(tmp_path: Path) -> None:
    state, store = _hosted(tmp_path)
    before = state.binding
    first = _store(store, "binary", b"build-1")
    second = _store(store, "binary", b"build-2")
    assert _run(state, binaryArtifact=first)[1] == 200
    assert _run(state, binaryArtifact=second)[1] == 200
    jobs = state.executor.jobs  # type: ignore[attr-defined]
    assert [j.overrides.binary for j in jobs] == [first, second]
    assert state.binding == before


def test_a_corrupt_scenarios_zip_is_refused_rather_than_crashing(tmp_path: Path) -> None:
    # `is_zipfile` reads only the end record, so a damaged central directory still gets this far.
    state, store = _hosted(tmp_path)
    blob = bytearray(_zip({"scenarios/added.yaml": _SCENARIO.encode()}))
    central = blob.rfind(b"PK\x01\x02")
    blob[central : central + 4] = b"XXXX"
    sha = _store(store, "scenarios", bytes(blob))
    resp, code = _run(state, scenario="added.yaml", scenariosArtifact=sha)
    assert code == 400
    assert "zip" in resp["error"]


def test_the_override_fields_stay_at_the_editor_role() -> None:
    # The role is decided by path alone, so naming an override never raises a run to admin.
    assert ops.required_role("POST", "/api/run") == "editor"


def test_run_set_refuses_either_override_field(tmp_path: Path) -> None:
    state, _ = _hosted(tmp_path)
    for field in ("binaryArtifact", "scenariosArtifact"):
        resp, code = ops.start_run_set(state, {"target": "demo", field: "b" * 64})
        assert code == 400
        assert field in resp["error"]


# --- placing an override into a job's own tree ------------------------------------------------

_MIXED_CONFIG = (
    "targets:\n"
    "  ios: { platform: ios, bundleId: com.example.demo, scenarios: ./scenarios,"
    " appPath: ./build/Demo.app }\n"
    "  android: { platform: android, package: com.example.demo, scenarios: ./scenarios,"
    " appPath: ./build/demo.apk }\n"
)


def _tree(root: Path, config: str = _CONFIG) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "bajutsu.config.yaml").write_text(config, encoding="utf-8")
    (root / "scenarios").mkdir()
    (root / "scenarios" / "bound.yaml").write_text(_SCENARIO, encoding="utf-8")
    return root


def _file(tmp_path: Path, name: str, blob: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(blob)
    return path


def test_a_binary_override_lands_at_the_jobs_own_target_alone(tmp_path: Path) -> None:
    root = _tree(tmp_path / "tree", _MIXED_CONFIG)
    (root / "build").mkdir()
    (root / "build" / "demo.apk").write_bytes(b"bound-apk")
    binary = _file(tmp_path, "binary", _zip({"Demo": b"\x7fELF"}))

    place_overrides(root, "ios", binary=binary, scenarios=None)

    assert (root / "build" / "Demo.app" / "Demo").read_bytes() == b"\x7fELF"
    assert (root / "build" / "demo.apk").read_bytes() == b"bound-apk"


def test_an_android_override_lands_at_its_own_app_path(tmp_path: Path) -> None:
    root = _tree(tmp_path / "tree", _MIXED_CONFIG)
    binary = _file(tmp_path, "binary", b"new-apk")

    place_overrides(root, "android", binary=binary, scenarios=None)

    assert (root / "build" / "demo.apk").read_bytes() == b"new-apk"
    assert not (root / "build" / "Demo.app").exists()


def test_a_zipped_app_bundle_replaces_the_bound_one_as_a_directory(tmp_path: Path) -> None:
    root = _tree(tmp_path / "tree")
    stale = root / "build" / "Demo.app"
    stale.mkdir(parents=True)
    (stale / "Stale").write_bytes(b"old")
    binary = _file(tmp_path, "binary", _zip({"Demo": b"\x7fELF"}))

    place_overrides(root, "demo", binary=binary, scenarios=None)

    assert (stale / "Demo").read_bytes() == b"\x7fELF"
    assert not (stale / "Stale").exists()


def test_a_scenarios_override_replaces_the_targets_scenarios_directory(tmp_path: Path) -> None:
    # A plain extraction deletes nothing, so without the replacement a scenario the branch removed
    # would still be in the directory for a fan-out to run.
    root = _tree(tmp_path / "tree")
    scenarios = _file(tmp_path, "scenarios", _zip({"scenarios/added.yaml": _SCENARIO.encode()}))

    (root / "build" / "Demo.app").mkdir(parents=True)
    (root / "build" / "Demo.app" / "Demo").write_bytes(b"bound")

    place_overrides(root, "demo", binary=None, scenarios=scenarios)

    assert sorted(p.name for p in (root / "scenarios").iterdir()) == ["added.yaml"]
    # The binary leg was not named, so the bound appPath stays byte for byte.
    assert (root / "build" / "Demo.app" / "Demo").read_bytes() == b"bound"


def test_a_scenarios_zip_cannot_reach_the_config(tmp_path: Path) -> None:
    # A `..` entry still lies under `scenarios/` by prefix alone, so the check reads the parts too.
    root = _tree(tmp_path / "tree")
    for name in ("bajutsu.config.yaml", "scenarios/../bajutsu.config.yaml"):
        scenarios = _file(
            tmp_path, "scenarios", _zip({name: b"targets: {}\n", "scenarios/a.yaml": b"x"})
        )
        with pytest.raises(CompositionError, match="outside"):
            place_overrides(root, "demo", binary=None, scenarios=scenarios)
    assert (root / "bajutsu.config.yaml").read_text(encoding="utf-8") == _CONFIG


def test_a_binary_override_for_a_target_with_no_app_path_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path / "tree")
    binary = _file(tmp_path, "binary", b"bin")
    with pytest.raises(CompositionError, match="appPath"):
        place_overrides(root, "web", binary=binary, scenarios=None)


def test_a_scenarios_zip_reaching_outside_the_scenarios_dir_is_refused(tmp_path: Path) -> None:
    # A scenarios-only override must not overwrite the bound binary its provenance never names.
    state, store = _hosted(tmp_path)
    blob = _zip({"scenarios/added.yaml": _SCENARIO.encode(), "build/Demo.app/Demo": b"x"})
    sha = _store(store, "scenarios", blob)
    resp, code = _run(state, scenario="added.yaml", scenariosArtifact=sha)
    assert code == 400
    assert "build/Demo.app/Demo" in resp["error"]


def test_a_target_whose_scenarios_dir_is_the_config_dir_refuses_a_scenarios_override(
    tmp_path: Path,
) -> None:
    state, store = _hosted(tmp_path)
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(_CONFIG.replace("scenarios: ./scenarios", "scenarios: ."), encoding="utf-8")
    sha = _store(store, "scenarios", _zip({"added.yaml": _SCENARIO.encode()}))
    resp, code = _run(state, scenario="added.yaml", scenariosArtifact=sha)
    assert code == 400
    assert "relative to its config" in resp["error"]


def test_placement_refuses_to_replace_the_whole_tree(tmp_path: Path) -> None:
    root = _tree(tmp_path / "tree", _CONFIG.replace("scenarios: ./scenarios", "scenarios: ."))
    scenarios = _file(tmp_path, "scenarios", _zip({"added.yaml": _SCENARIO.encode()}))
    with pytest.raises(CompositionError, match="whole tree"):
        place_overrides(root, "demo", binary=None, scenarios=scenarios)
    assert (root / "bajutsu.config.yaml").is_file()


def test_placement_refuses_to_delete_an_app_path_nested_in_the_scenarios_dir(
    tmp_path: Path,
) -> None:
    config = _CONFIG.replace("./build/Demo.app", "./scenarios/Demo.app")
    root = _tree(tmp_path / "tree", config)
    scenarios = _file(tmp_path, "scenarios", _zip({"scenarios/added.yaml": _SCENARIO.encode()}))
    with pytest.raises(CompositionError, match="would delete"):
        place_overrides(root, "demo", binary=None, scenarios=scenarios)
    assert (root / "scenarios" / "bound.yaml").is_file()


def test_placement_refuses_a_zip_entry_outside_the_scenarios_dir(tmp_path: Path) -> None:
    # The dispatch gate refuses this first; the worker re-checks rather than trusting a queued job.
    root = _tree(tmp_path / "tree")
    scenarios = _file(
        tmp_path,
        "scenarios",
        _zip({"scenarios/added.yaml": _SCENARIO.encode(), "baselines/home.png": b"png"}),
    )
    with pytest.raises(CompositionError, match="outside scenarios/"):
        place_overrides(root, "demo", binary=None, scenarios=scenarios)
    assert not (root / "baselines").exists()


def test_overrides_naming_no_leg_are_not_a_valid_value() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ArtifactOverrides("demo")


def test_placement_refuses_to_delete_baselines_sharing_the_scenarios_dir(tmp_path: Path) -> None:
    config = _CONFIG.replace(
        "scenarios: ./scenarios,", "scenarios: ./scenarios, baselines: ./scenarios,"
    )
    root = _tree(tmp_path / "tree", config)
    scenarios = _file(tmp_path, "scenarios", _zip({"scenarios/added.yaml": _SCENARIO.encode()}))
    with pytest.raises(CompositionError, match="would delete"):
        place_overrides(root, "demo", binary=None, scenarios=scenarios)
    assert (root / "scenarios" / "bound.yaml").is_file()
