"""Tests for the BaselineStore seam (BE-0015 visual-regression baselines).

The local store confines baselines to a dir; the object store keeps them under
``<prefix>baselines/``. Both share the same contract (open_bytes / write / names) and the same
name guard, so a crafted name can't escape either.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.serve.baselines import LocalBaselineStore
from bajutsu.serve.server.baselines import ObjectBaselineStore


class _FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, key: str) -> bool:
        return key in self.objects

    def get_bytes(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def put_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def put_file(self, key: str, path: Path) -> None:
        self.objects[key] = path.read_bytes()

    def presigned_url(self, key: str) -> str:
        return f"https://signed.example/{key}"

    def list_keys(self, prefix: str) -> list[str]:
        return [k for k in self.objects if k.startswith(prefix)]


def test_local_baseline_store_round_trips_and_lists(tmp_path: Path) -> None:
    store = LocalBaselineStore(tmp_path / "baselines")
    written = store.write("home.png", b"\x89PNG")  # keep the write out of the assert (-O strips it)
    assert written == "home.png"
    assert (tmp_path / "baselines" / "home.png").read_bytes() == b"\x89PNG"
    assert store.open_bytes("home.png") == b"\x89PNG"
    assert store.open_bytes("missing.png") is None
    assert store.names() == ["home.png"]


def test_local_baseline_store_rejects_escapes(tmp_path: Path) -> None:
    store = LocalBaselineStore(tmp_path / "baselines")
    for bad in ("../evil.png", "/abs.png", "a\x00.png", ""):
        written = store.write(bad, b"x")
        assert written is None, bad
        assert store.open_bytes(bad) is None, bad
    assert not (tmp_path / "evil.png").exists()


def test_object_baseline_store_round_trips_under_prefix() -> None:
    obj = _FakeObjectStore()
    store = ObjectBaselineStore(obj, prefix="tenant/")
    written = store.write("home.png", b"\x89PNG")  # keep the write out of the assert (-O strips it)
    assert written == "home.png"
    assert obj.objects["tenant/baselines/home.png"] == b"\x89PNG"
    assert store.open_bytes("home.png") == b"\x89PNG"
    assert store.names() == ["home.png"]
    rejected = store.write("../evil.png", b"x")  # unsafe name rejected
    assert rejected is None


def test_approve_writes_to_the_baseline_store(tmp_path: Path) -> None:
    # approve_baseline reads the screenshot from the artifact store and writes it via the baseline
    # store — so on the server it lands in object storage, not the filesystem.
    from bajutsu import serve as srv
    from bajutsu.serve import operations as ops

    runs = tmp_path / "runs"
    (runs / "r1" / "00-home").mkdir(parents=True)
    (runs / "r1" / "00-home" / "visual-actual.png").write_bytes(b"PNG")
    state = srv.ServeState(runs_dir=runs, cwd=tmp_path)
    obj = _FakeObjectStore()
    state.baselines = ObjectBaselineStore(obj)

    payload, code = ops.approve_baseline(
        state, {"runId": "r1", "sid": "00-home", "baseline": "home.png"}
    )
    assert code == 200 and payload["ok"] is True
    assert obj.objects["baselines/home.png"] == b"PNG"

    bad, code = ops.approve_baseline(
        state, {"runId": "r1", "sid": "00-home", "baseline": "../escape.png"}
    )
    assert code == 400 and "escapes" in bad["error"]
