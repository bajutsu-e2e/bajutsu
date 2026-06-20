"""Tests for the storage-backed ScenarioStore (BE-0015 server phase).

`StorageScenarioStore` is the server implementation of the `ScenarioStore` seam: the control plane
resolves a project's scenarios by name from per-project storage (a DB / object storage), never from
a client-chosen filesystem path. It serves the authoring operations the UI needs — `list`, `read`,
`save` — delegating to an injected `ScenarioStorage`, so an in-memory fake drives this on the gate.
The execution paths (`resolve_runnable` / `out_path`) are worker-side and not served here.
"""

from __future__ import annotations

import pytest

from bajutsu.serve.server.scenarios import StorageScenarioStore

SCENARIO = "- name: a\n  steps: []\n"


class FakeScenarioStorage:
    """Per-project scenario storage, in memory: {app: {ref: yaml}}."""

    def __init__(self, projects: dict[str, dict[str, str]]) -> None:
        self._projects = projects

    def has_app(self, app: str) -> bool:
        return app in self._projects

    def list(self, app: str) -> list[dict[str, object]]:
        return [{"file": ref, "path": ref} for ref in sorted(self._projects.get(app, {}))]

    def read(self, app: str, ref: str | None) -> str | None:
        return self._projects.get(app, {}).get(ref or "")

    def save(self, app: str, ref: str | None, text: str) -> str | None:
        if app not in self._projects or not ref or not ref.endswith(".yaml"):
            return None
        self._projects[app][ref] = text
        return ref


def test_scope_is_none_for_unknown_or_missing_app() -> None:
    store = StorageScenarioStore(FakeScenarioStorage({"demo": {}}))
    assert store.scope("ghost") is None
    assert store.scope(None) is None  # a project must be named (no filesystem fallback)
    assert store.scope("demo") is not None


def test_list_read_save_delegate_to_storage() -> None:
    storage = FakeScenarioStorage({"demo": {"smoke.yaml": SCENARIO}})
    scope = StorageScenarioStore(storage).scope("demo")
    assert scope is not None
    assert [s["file"] for s in scope.list()] == ["smoke.yaml"]
    assert scope.read("smoke.yaml") == SCENARIO
    assert scope.read("missing.yaml") is None
    assert scope.save("new.yaml", "- name: b\n  steps: []\n") == "new.yaml"
    assert scope.read("new.yaml") == "- name: b\n  steps: []\n"
    assert scope.save("bad.txt", "x") is None  # storage rejects a non-scenario ref


def test_execution_paths_are_worker_side() -> None:
    # run/record materialize the scenario on the worker, so the control-plane store doesn't serve
    # the filesystem-path methods.
    scope = StorageScenarioStore(FakeScenarioStorage({"demo": {}})).scope("demo")
    assert scope is not None
    with pytest.raises(NotImplementedError):
        scope.resolve_runnable("smoke.yaml")
    with pytest.raises(NotImplementedError):
        scope.out_path("authored")
