"""Tests for the storage-backed ScenarioStore (BE-0015 server phase).

`StorageScenarioStore` is the server implementation of the `ScenarioStore` seam: the control plane
resolves a project's scenarios by name from per-project storage (a DB / object storage), never from
a client-chosen filesystem path. It serves the authoring operations the UI needs — `list`, `read`,
`save` — delegating to an injected `ScenarioStorage`, so an in-memory fake drives this on the gate.
`runnable` ships the scenario as materials for a remote worker; `out_path` (record) stays worker-side.

`LocalTreeScenarioStorage` (BE-0324) is a second `ScenarioStorage`: `list`/`read` resolve the bound
config's already-extracted local-tree dir instead of an object-storage bucket, while `save` still
delegates to an injected `ObjectScenarioStorage` (the write side is out of this item's scope).
"""

from __future__ import annotations

from pathlib import Path

from _shared import FakeObjectStore

from bajutsu.serve.server.scenarios import (
    LocalTreeScenarioStorage,
    ObjectScenarioStorage,
    StorageScenarioStore,
)
from bajutsu.serve.state import ServeState

SCENARIO = "- name: a\n  steps: []\n"


class FakeScenarioStorage:
    """Per-project scenario storage, in memory: {app: {ref: yaml}}."""

    def __init__(self, projects: dict[str, dict[str, str]]) -> None:
        self._projects = projects

    def has_app(self, app: str, *, session: str | None = None, org: str = "") -> bool:
        return app in self._projects

    def list(
        self, app: str, *, session: str | None = None, org: str = ""
    ) -> list[dict[str, object]]:
        return [{"file": ref, "path": ref} for ref in sorted(self._projects.get(app, {}))]

    def read(
        self, app: str, ref: str | None, *, session: str | None = None, org: str = ""
    ) -> str | None:
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


class PermissiveStorage:
    """A storage that would return for *any* ref — so a leak proves the scope didn't pre-reject."""

    def has_app(self, app: str, *, session: str | None = None, org: str = "") -> bool:
        return True

    def list(
        self, app: str, *, session: str | None = None, org: str = ""
    ) -> list[dict[str, object]]:
        return []

    def read(
        self, app: str, ref: str | None, *, session: str | None = None, org: str = ""
    ) -> str | None:
        return "LEAK"

    def save(self, app: str, ref: str | None, text: str) -> str | None:
        return "LEAK"


def test_unsafe_refs_are_rejected_before_storage() -> None:
    # A ref is a trust boundary even with no filesystem (object-store key / DB id): the scope must
    # reject obviously unsafe refs before delegating, so a backing store never sees them.
    scope = StorageScenarioStore(PermissiveStorage()).scope("demo")
    assert scope is not None
    for bad in ("", "note.txt", "../smoke.yaml", "/abs/smoke.yaml", "a\x00.yaml", None):
        assert scope.read(bad) is None, bad
        assert scope.save(bad, SCENARIO) is None, bad
    assert scope.read("smoke.yaml") == "LEAK"  # a safe ref still reaches storage


def test_runnable_ships_the_scenario_as_materials() -> None:
    # No path exists on the control plane: runnable resolves by name from storage and returns the
    # text as a material the worker writes, with a workspace-relative `--scenario` arg.
    scope = StorageScenarioStore(FakeScenarioStorage({"demo": {"smoke.yaml": SCENARIO}})).scope(
        "demo"
    )
    assert scope is not None
    runnable = scope.runnable("smoke.yaml")
    assert runnable is not None
    assert runnable.arg == "scenarios/smoke.yaml"
    assert runnable.materials == {"scenarios/smoke.yaml": SCENARIO}
    assert scope.runnable("missing.yaml") is None  # not in storage
    assert scope.runnable("../escape.yaml") is None  # unsafe ref
    # Only the basename is honoured, for both separators — a leading dir (incl. a Windows
    # backslash) can't leak into the storage key.
    assert scope.runnable("sub/smoke.yaml") == runnable
    assert scope.runnable("sub\\smoke.yaml") == runnable


def test_authored_targets_a_workspace_path_and_storage_ref() -> None:
    # No filesystem on the control plane: authored() gives a workspace-relative --out and the
    # (app, ref) the worker persists the result to.
    scope = StorageScenarioStore(FakeScenarioStorage({"demo": {}})).scope("demo")
    assert scope is not None
    authored = scope.authored("login flow")
    assert authored.out == "scenarios/login flow.yaml"
    assert authored.save == ("demo", "login flow.yaml")


def test_authored_stamps_a_taken_ref_to_avoid_clobber() -> None:
    scope = StorageScenarioStore(FakeScenarioStorage({"demo": {"smoke.yaml": "x"}})).scope("demo")
    assert scope is not None
    authored = scope.authored("smoke")  # smoke.yaml exists -> stamped
    assert authored.save is not None
    app, ref = authored.save
    assert (
        app == "demo" and ref != "smoke.yaml" and ref.startswith("smoke-") and ref.endswith(".yaml")
    )


class _PoisonObjectStore(FakeObjectStore):
    """An ObjectStore that fails on any read, so a call proves list/read leaked to object storage."""

    def get_bytes(self, key: str) -> bytes | None:
        raise AssertionError(f"list/read must never reach object storage (key {key!r})")

    def list_keys(self, prefix: str) -> list[str]:
        raise AssertionError(f"list/read must never reach object storage (prefix {prefix!r})")


def _tree_state(scenarios_dir: Path) -> ServeState:
    """A ServeState whose scenarios dir is fixed to *scenarios_dir* (bypasses config resolution),
    matching how `_scenarios_dir_for` short-circuits on `state.scenarios_dir`."""
    return ServeState(runs_dir=scenarios_dir.parent / "runs", scenarios_dir=scenarios_dir)


def test_local_tree_has_app_delegates_to_the_save_storage(tmp_path: Path) -> None:
    # has_app has no local-tree counterpart (there is no "list of apps" on disk to check): it
    # delegates to the injected ObjectScenarioStorage's own apps lookup, so the two never disagree.
    save_storage = ObjectScenarioStorage(
        _PoisonObjectStore(), lambda _session, _org: {"demo", "other"}
    )
    storage = LocalTreeScenarioStorage(_tree_state(tmp_path), save_storage)
    assert storage.has_app("demo") is True
    assert storage.has_app("ghost") is False


def test_local_tree_list_and_read_resolve_from_disk_not_object_storage(tmp_path: Path) -> None:
    # A Git- or zip-sourced scenario already sits on this dir (BE-0324's motivation); list/read must
    # find it there and never fall through to (or need) object storage, proven by the poison store.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(SCENARIO, encoding="utf-8")
    save_storage = ObjectScenarioStorage(_PoisonObjectStore(), lambda _session, _org: {"demo"})
    storage = LocalTreeScenarioStorage(_tree_state(scn_dir), save_storage)
    listed = storage.list("demo")
    assert [s["file"] for s in listed] == ["smoke.yaml"]
    # `path` is normalized to the ref (not the resolved on-disk path): read/save, and the UI's
    # read-back-by-path round trip, take a ref — never a control-plane filesystem path (BE-0051).
    assert listed[0]["path"] == "smoke.yaml"
    assert storage.read("demo", listed[0]["path"]) == SCENARIO
    assert storage.read("demo", "smoke.yaml") == SCENARIO
    assert storage.read("demo", "missing.yaml") is None


def test_local_tree_list_and_read_are_empty_without_a_resolvable_dir() -> None:
    # No `scenarios_dir` override and no bound config: `_scenarios_dir_for` returns None, so there is
    # no local scope to delegate to (matching how the local backend behaves for the same state).
    state = ServeState(runs_dir=Path("runs"))
    save_storage = ObjectScenarioStorage(_PoisonObjectStore(), lambda _session, _org: {"demo"})
    storage = LocalTreeScenarioStorage(state, save_storage)
    assert storage.list("demo") == []
    assert storage.read("demo", "smoke.yaml") is None


def test_local_tree_list_and_read_are_empty_when_the_resolved_dir_is_missing(
    tmp_path: Path,
) -> None:
    # `_scenarios_dir_for` resolves to a real Path even before extraction has landed anything there
    # (or a target's `scenarios:` dir was never created) — distinct from the None case above.
    missing = tmp_path / "scenarios"  # deliberately never created
    save_storage = ObjectScenarioStorage(_PoisonObjectStore(), lambda _session, _org: {"demo"})
    storage = LocalTreeScenarioStorage(_tree_state(missing), save_storage)
    assert storage.list("demo") == []
    assert storage.read("demo", "smoke.yaml") is None


def test_local_tree_list_filters_out_refs_read_would_reject(tmp_path: Path) -> None:
    # list must only show what read/save would accept, matching ObjectScenarioStorage.list's
    # contract — a glob can surface a *.yaml name valid_scenario_ref rejects. A literal backslash is
    # legal in a POSIX filename (it isn't a path separator there) but normalizes to a `..` traversal
    # under valid_scenario_ref's Windows-separator handling.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(SCENARIO, encoding="utf-8")
    (scn_dir / "a\\..\\b.yaml").write_text("- name: x\n  steps: []\n", encoding="utf-8")
    save_storage = ObjectScenarioStorage(_PoisonObjectStore(), lambda _session, _org: {"demo"})
    storage = LocalTreeScenarioStorage(_tree_state(scn_dir), save_storage)
    assert [s["file"] for s in storage.list("demo")] == ["smoke.yaml"]


def test_local_tree_read_degrades_instead_of_raising_on_a_bad_file(tmp_path: Path) -> None:
    # A file that exists but can't be decoded must not crash the UI/dispatch (matching
    # ObjectScenarioStorage.read's leniency) — read degrades to "not found" instead of raising.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "bad.yaml").write_bytes(b"\xff\xfe not utf-8")
    save_storage = ObjectScenarioStorage(_PoisonObjectStore(), lambda _session, _org: {"demo"})
    storage = LocalTreeScenarioStorage(_tree_state(scn_dir), save_storage)
    assert storage.read("demo", "bad.yaml") is None


def test_local_tree_save_delegates_to_the_injected_object_storage(tmp_path: Path) -> None:
    # save has no local-tree counterpart yet (BE-0324 scopes the read path only): it must still
    # reach the injected ObjectScenarioStorage, and the write must not appear in the local tree.
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    object_store = FakeObjectStore()
    save_storage = ObjectScenarioStorage(object_store, lambda _session, _org: {"demo"})
    storage = LocalTreeScenarioStorage(_tree_state(scn_dir), save_storage)
    assert storage.save("demo", "new.yaml", SCENARIO) == "new.yaml"
    assert object_store.objects["scenarios/demo/new.yaml"] == SCENARIO.encode()
    assert storage.read("demo", "new.yaml") is None  # lands in object storage, not the local tree
    assert storage.list("demo") == []


def test_local_tree_storage_drives_the_scenario_store_seam(tmp_path: Path) -> None:
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(SCENARIO, encoding="utf-8")
    save_storage = ObjectScenarioStorage(_PoisonObjectStore(), lambda _session, _org: {"demo"})
    storage = LocalTreeScenarioStorage(_tree_state(scn_dir), save_storage)
    scope = StorageScenarioStore(storage).scope("demo")
    assert scope is not None
    assert scope.read("smoke.yaml") == SCENARIO
    # runnable() ships the local-tree text as materials, unchanged from the object-storage backing.
    runnable = scope.runnable("smoke.yaml")
    assert runnable is not None
    assert runnable.arg == "scenarios/smoke.yaml"
    assert runnable.materials == {"scenarios/smoke.yaml": SCENARIO}
