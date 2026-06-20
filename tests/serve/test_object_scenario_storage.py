"""Tests for the object-storage-backed ScenarioStorage (BE-0015 server phase).

`ObjectScenarioStorage` is the real backing for `StorageScenarioStore`'s `ScenarioStorage` slice:
scenarios live in S3-compatible object storage (R2) at ``<prefix>scenarios/<app>/<name>.yaml``. The
object-store client is injected, so an in-memory fake drives the contract here. The known projects
come from the control plane's configured apps (no Postgres registry in the single-tenant path), and
*prefix* is parameterized so a tenant prefix can scope a shared bucket later without a contract
change.
"""

from __future__ import annotations

from bajutsu.serve.server.scenarios import ObjectScenarioStorage, StorageScenarioStore

SCENARIO = "description: smoke\nscenarios:\n  - name: alpha\n    steps: []\n"


class _FakeObjectStore:
    """The ObjectStore slice ObjectScenarioStorage uses, in memory."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})

    def exists(self, key: str) -> bool:
        return key in self.objects

    def get_bytes(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def put_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def presigned_url(self, key: str) -> str:
        return f"https://signed.example/{key}"

    def list_keys(self, prefix: str) -> list[str]:
        return [k for k in self.objects if k.startswith(prefix)]


def test_has_app_comes_from_the_configured_apps() -> None:
    storage = ObjectScenarioStorage(_FakeObjectStore(), lambda: {"demo", "other"})
    assert storage.has_app("demo") is True
    assert storage.has_app("ghost") is False


def test_save_then_read_round_trips_under_the_app_prefix() -> None:
    store = _FakeObjectStore()
    storage = ObjectScenarioStorage(store, lambda: {"demo"})
    assert storage.save("demo", "smoke.yaml", SCENARIO) == "smoke.yaml"
    assert store.objects["scenarios/demo/smoke.yaml"] == SCENARIO.encode()
    assert storage.read("demo", "smoke.yaml") == SCENARIO
    assert storage.read("demo", "missing.yaml") is None
    assert storage.read("demo", None) is None
    assert storage.save("demo", None, "x") is None


def test_list_summarizes_only_the_apps_direct_yaml_children() -> None:
    store = _FakeObjectStore(
        {
            "scenarios/demo/smoke.yaml": SCENARIO.encode(),
            "scenarios/demo/sub/nested.yaml": b"- name: x\n  steps: []\n",  # not a direct child
            "scenarios/demo/notes.txt": b"x",  # not a *.yaml
            "scenarios/other/theirs.yaml": b"- name: y\n  steps: []\n",  # a different app
        }
    )
    storage = ObjectScenarioStorage(store, lambda: {"demo", "other"})
    listed = storage.list("demo")
    assert [s["file"] for s in listed] == ["smoke.yaml"]
    assert listed[0]["names"] == ["alpha"] and listed[0]["description"] == "smoke"
    assert listed[0]["path"] == "smoke.yaml"  # the ref the run/read commands take


def test_prefix_scopes_a_shared_bucket() -> None:
    store = _FakeObjectStore()
    storage = ObjectScenarioStorage(store, lambda: {"demo"}, prefix="org1/")
    storage.save("demo", "smoke.yaml", SCENARIO)
    assert "org1/scenarios/demo/smoke.yaml" in store.objects
    assert storage.read("demo", "smoke.yaml") == SCENARIO


def test_drives_the_scenario_store_seam() -> None:
    # The object backing satisfies the ScenarioStorage slice StorageScenarioStore consumes.
    store = _FakeObjectStore({"scenarios/demo/smoke.yaml": SCENARIO.encode()})
    scope = StorageScenarioStore(ObjectScenarioStorage(store, lambda: {"demo"})).scope("demo")
    assert scope is not None
    assert scope.read("smoke.yaml") == SCENARIO
    assert (
        StorageScenarioStore(ObjectScenarioStorage(store, lambda: {"demo"})).scope("ghost") is None
    )
