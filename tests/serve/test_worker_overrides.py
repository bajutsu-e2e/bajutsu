"""Tests for placing a job's per-job artifact overrides on the worker (BE-0431).

The lease signs a GET per named override, and the worker builds the job a tree of its own — a copy
of the cached bundle, or a directory for a materials-based job — with the overrides placed in it.
Keying that tree by the overrides is what keeps one job's artifacts from reaching the next job this
worker leases. Pure packaging over a fake HTTP fetch: no network, no device.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from bajutsu.serve.cli import worker as worker_cli

_CONFIG = (
    "defaults: { backend: [ios] }\n"
    "targets:\n"
    "  demo: { bundleId: com.example.demo, scenarios: ./scenarios, appPath: ./build/Demo.app }\n"
)
_SCENARIO = b"- name: a\n  steps: []\n"


def _digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


_BINARY = _zip({"Demo": b"\x7fELF-override"})
_SCENARIOS = _zip({"scenarios/added.yaml": _SCENARIO})
_BUNDLE = _zip(
    {
        "bajutsu.config.yaml": _CONFIG.encode(),
        "scenarios/bound.yaml": _SCENARIO,
        "build/Demo.app/Demo": b"\x7fELF-bound",
    }
)
_URLS = {
    "https://signed/binary": _BINARY,
    "https://signed/scenarios": _SCENARIOS,
    "https://signed/bundle": _BUNDLE,
}
_OVERRIDE_URLS = {"binary": "https://signed/binary", "scenarios": "https://signed/scenarios"}


def _serve_urls(monkeypatch: pytest.MonkeyPatch, bodies: dict[str, bytes]) -> list[str]:
    fetched: list[str] = []

    def fake_get(url: str, dest: Path, *, timeout: float | None = None) -> None:
        fetched.append(url)
        dest.write_bytes(bodies[url])

    monkeypatch.setattr(worker_cli, "_get_file", fake_get)
    return fetched


def _overrides(*, binary: bytes | None = None, scenarios: bytes | None = None) -> dict[str, Any]:
    return {
        "target": "demo",
        "binary": _digest(binary) if binary is not None else None,
        "scenarios": _digest(scenarios) if scenarios is not None else None,
    }


def _materials_spec(
    config: str = _CONFIG, *, scenario_material: str | None = None, **legs: bytes
) -> dict[str, Any]:
    materials = {"bajutsu.config.yaml": config}
    if scenario_material is not None:
        materials["scenarios/bound.yaml"] = scenario_material
    return {
        "org": "acme",
        "materials": materials,
        "overrides": _overrides(**legs),
    }


def _bundle_spec(**legs: bytes) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "org": "acme",
        "bundle": {"id": _digest(_BUNDLE), "artifacts": None, "scenarios_filename": None},
        "materials": {"bajutsu.config.yaml": _CONFIG},
    }
    if legs:
        spec["overrides"] = _overrides(**legs)
    return spec


def _workspace(work: Path, spec: dict[str, Any], urls: dict[str, str] | None = None) -> Path:
    job_work, failure = worker_cli._workspace_or_failure(
        work, spec, {"bundle": "https://signed/bundle"}, _OVERRIDE_URLS if urls is None else urls
    )
    assert failure is None, failure
    return job_work


def test_a_materials_job_gets_the_binary_it_never_carried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A materials-based job has no bundle, so this is the only path a binary has onto the worker.
    _serve_urls(monkeypatch, _URLS)

    job_work = _workspace(tmp_path, _materials_spec(binary=_BINARY))

    assert job_work != tmp_path
    assert (job_work / "bajutsu.config.yaml").read_text(encoding="utf-8") == _CONFIG
    assert (job_work / "build" / "Demo.app" / "Demo").read_bytes() == b"\x7fELF-override"


def test_a_bundle_job_installs_the_overrides_over_its_bound_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_urls(monkeypatch, _URLS)

    job_work = _workspace(tmp_path, _bundle_spec(binary=_BINARY, scenarios=_SCENARIOS))

    assert (job_work / "build" / "Demo.app" / "Demo").read_bytes() == b"\x7fELF-override"
    assert sorted(p.name for p in (job_work / "scenarios").iterdir()) == ["added.yaml"]


def test_an_override_reuses_the_cached_bundle_rather_than_fetching_it_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched = _serve_urls(monkeypatch, _URLS)
    _workspace(tmp_path, _bundle_spec())
    assert fetched == ["https://signed/bundle"]

    _workspace(tmp_path, _bundle_spec(binary=_BINARY))
    _workspace(tmp_path, _bundle_spec(binary=_BINARY))

    # The bundle was fetched once; the override once; the repeat job reused its own tree.
    assert fetched == ["https://signed/bundle", "https://signed/binary"]


def test_a_job_with_no_override_after_one_that_had_it_runs_its_own_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The worker keeps one working directory for its whole lifetime, so the override must not have
    # landed anywhere the next, override-free job starts from.
    _serve_urls(monkeypatch, _URLS)
    overridden = _workspace(tmp_path, _bundle_spec(binary=_BINARY, scenarios=_SCENARIOS))

    plain = _workspace(tmp_path, _bundle_spec())

    assert plain != overridden
    assert (plain / "build" / "Demo.app" / "Demo").read_bytes() == b"\x7fELF-bound"
    assert sorted(p.name for p in (plain / "scenarios").iterdir()) == ["bound.yaml"]


def test_a_materials_job_with_no_override_keeps_the_shared_directory(tmp_path: Path) -> None:
    spec = {"org": "acme", "materials": {"bajutsu.config.yaml": _CONFIG}}
    assert _workspace(tmp_path, spec, urls={}) == tmp_path


def test_two_materials_jobs_whose_configs_differ_get_separate_workspaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_urls(monkeypatch, _URLS)
    other = _CONFIG.replace("./build/Demo.app", "./out/Other.app")

    first = _workspace(tmp_path, _materials_spec(binary=_BINARY))
    second = _workspace(tmp_path, _materials_spec(other, binary=_BINARY))

    assert first != second
    assert (second / "out" / "Other.app" / "Demo").is_file()
    assert not (first / "out").exists()


def test_two_materials_jobs_sharing_a_binary_but_not_scenario_text_share_a_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A CI fan-out running many scenarios against one binary override ships the same config but a
    # different bound scenario's text per job (`storage_scenario_scope.runnable`). The tree is keyed
    # on the config alone, so this must not re-fetch the binary once per scenario.
    fetched = _serve_urls(monkeypatch, _URLS)

    first = _workspace(tmp_path, _materials_spec(binary=_BINARY, scenario_material="- name: a\n"))
    second = _workspace(tmp_path, _materials_spec(binary=_BINARY, scenario_material="- name: b\n"))

    assert first == second
    assert fetched == ["https://signed/binary"]


def test_jobs_sharing_a_binary_but_not_scenarios_get_separate_workspaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_scenarios = _zip({"scenarios/other.yaml": _SCENARIO})
    _serve_urls(monkeypatch, {**_URLS, "https://signed/other": other_scenarios})

    first = _workspace(tmp_path, _materials_spec(binary=_BINARY, scenarios=_SCENARIOS))
    second = _workspace(
        tmp_path,
        _materials_spec(binary=_BINARY, scenarios=other_scenarios),
        urls={"binary": "https://signed/binary", "scenarios": "https://signed/other"},
    )

    assert first != second
    assert sorted(p.name for p in (second / "scenarios").iterdir()) == ["other.yaml"]


def test_an_override_the_lease_could_not_sign_fails_the_job(tmp_path: Path) -> None:
    _job_work, failure = worker_cli._workspace_or_failure(
        tmp_path, _materials_spec(binary=_BINARY), None, {}
    )
    assert failure is not None
    assert "signed no url" in failure["error"]


def test_a_gone_override_fails_the_job_and_leaves_nothing_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(url: str, dest: Path, *, timeout: float | None = None) -> None:
        raise HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_cli, "_get_file", gone)

    _job_work, failure = worker_cli._workspace_or_failure(
        tmp_path, _materials_spec(binary=_BINARY), None, _OVERRIDE_URLS
    )

    assert failure is not None
    assert "override" in failure["error"]
    trees = tmp_path / worker_cli._OVERRIDE_CACHE_DIR / "acme"
    assert not [p for p in trees.iterdir() if not p.name.startswith(".")]


def test_a_mismatched_override_is_transient_so_the_lease_lapses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A truncated download is not the artifact being gone: another attempt can still succeed.
    _serve_urls(monkeypatch, {"https://signed/binary": b"truncated"})
    with pytest.raises(worker_cli._TransientFetch):
        worker_cli._workspace_or_failure(
            tmp_path, _materials_spec(binary=_BINARY), None, _OVERRIDE_URLS
        )


def test_an_invalid_override_digest_is_refused_before_it_becomes_a_key(tmp_path: Path) -> None:
    spec = _materials_spec(binary=_BINARY)
    spec["overrides"]["binary"] = "../escape"
    _job_work, failure = worker_cli._workspace_or_failure(tmp_path, spec, None, _OVERRIDE_URLS)
    assert failure is not None
    assert "invalid binary override" in failure["error"]


def test_the_lease_response_urls_map_onto_override_kinds() -> None:
    body = {"binary_url": "https://signed/binary", "bundle_urls": {"bundle": "x"}}
    assert worker_cli._override_urls(body) == {"binary": "https://signed/binary"}


def test_an_overrides_block_naming_no_leg_is_no_override(tmp_path: Path) -> None:
    spec = {"org": "acme", "overrides": {"target": "demo", "binary": None, "scenarios": None}}
    assert _workspace(tmp_path, spec, urls={}) == tmp_path


def test_a_malformed_overrides_block_fails_rather_than_running_the_binding(tmp_path: Path) -> None:
    # Running the bound binary here would contradict the manifest's recorded override provenance.
    spec = {"org": "acme", "overrides": ["binary"]}
    _job_work, failure = worker_cli._workspace_or_failure(tmp_path, spec, None, {})
    assert failure is not None
    assert "malformed overrides" in failure["error"]


def test_overrides_naming_no_target_fail_the_job(tmp_path: Path) -> None:
    spec = _materials_spec(binary=_BINARY)
    spec["overrides"]["target"] = ""
    _job_work, failure = worker_cli._workspace_or_failure(tmp_path, spec, None, _OVERRIDE_URLS)
    assert failure is not None
    assert "names no target" in failure["error"]


def test_a_scenarios_only_override_keeps_the_bundles_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_urls(monkeypatch, _URLS)

    job_work = _workspace(tmp_path, _bundle_spec(scenarios=_SCENARIOS))

    assert (job_work / "build" / "Demo.app" / "Demo").read_bytes() == b"\x7fELF-bound"
    assert sorted(p.name for p in (job_work / "scenarios").iterdir()) == ["added.yaml"]


def test_a_concurrent_build_of_the_same_tree_keeps_the_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another job with the same key finishes first; this build drops its own copy and runs from the
    # winner's equivalent tree.
    _serve_urls(monkeypatch, _URLS)
    from bajutsu.serve.operations.composition import place_overrides as real_place

    def place_then_lose_the_race(root: Path, target: str, **legs: Any) -> None:
        real_place(root, target, **legs)
        key = root.name[1:].split(".tmp-")[0]
        (root.parent / key).mkdir()
        (root.parent / key / "winner").touch()

    monkeypatch.setattr(worker_cli, "place_overrides", place_then_lose_the_race)

    job_work = _workspace(tmp_path, _materials_spec(binary=_BINARY))

    assert (job_work / "winner").is_file()
    assert [p for p in job_work.parent.iterdir() if ".tmp-" in p.name] == []
