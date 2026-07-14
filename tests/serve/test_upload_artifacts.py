"""Tests for the per-artifact content-addressed cache (bajutsu/serve/upload_artifacts.py, BE-0268).

Each of config/scenarios/binary is cached identically as raw bytes — no zip-specific handling here,
that's `materialize_composition`'s job (see test_composition.py). Pure packaging: no device, no AI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.serve.upload_artifacts import (
    ARTIFACT_KINDS,
    artifact_store_key,
    local_artifact_dir,
    materialize_artifact,
)


def _written(tmp_path: Path, name: str, blob: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(blob)
    return p


def test_artifact_kinds_is_the_closed_three_way_set() -> None:
    assert set(ARTIFACT_KINDS) == {"config", "scenarios", "binary"}


def test_artifact_store_key_nests_by_kind_under_uploads() -> None:
    key = artifact_store_key("", "default", "config", "abc123")
    assert key == "uploads/config/abc123"


def test_artifact_store_key_org_scopes_and_never_collides_with_legacy_bundle_key() -> None:
    key = artifact_store_key("", "acme", "binary", "abc123")
    assert key == "acme/uploads/binary/abc123"
    # A legacy combined-bundle key for the same sha ends in ".zip" and has no kind segment — the two
    # can never collide even for an identical sha256.
    assert key != "acme/uploads/abc123.zip"


def test_local_artifact_dir_is_kind_scoped() -> None:
    root = Path("/cache")
    assert local_artifact_dir(root, "default", "config") == root / "config"
    assert local_artifact_dir(root, "acme", "config") == root / "config" / "acme"


def test_materialize_artifact_copies_on_a_miss(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "cache"
    src = _written(tmp_path, "src.bin", b"hello world")
    dest = materialize_artifact(src, artifacts_dir, "default", "binary", "abc123")
    assert dest == local_artifact_dir(artifacts_dir, "default", "binary") / "abc123"
    assert dest.read_bytes() == b"hello world"
    # No leftover temp file beside the resolved entry.
    assert [p.name for p in dest.parent.iterdir()] == ["abc123"]


def test_materialize_artifact_reuses_an_existing_entry(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "cache"
    src = _written(tmp_path, "src.bin", b"hello world")
    materialize_artifact(src, artifacts_dir, "default", "binary", "abc123")
    src.unlink()  # a cache hit must not need the source file again
    dest = materialize_artifact(src, artifacts_dir, "default", "binary", "abc123")
    assert dest.read_bytes() == b"hello world"


def test_materialize_artifact_kinds_never_collide(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "cache"
    src = _written(tmp_path, "src.bin", b"config bytes")
    config_dest = materialize_artifact(src, artifacts_dir, "default", "config", "abc123")
    src.write_bytes(b"binary bytes")
    binary_dest = materialize_artifact(src, artifacts_dir, "default", "binary", "abc123")
    assert config_dest != binary_dest
    assert config_dest.read_bytes() == b"config bytes"
    assert binary_dest.read_bytes() == b"binary bytes"


def test_materialize_artifact_orgs_never_collide(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "cache"
    src = _written(tmp_path, "src.bin", b"acme bytes")
    acme_dest = materialize_artifact(src, artifacts_dir, "acme", "binary", "abc123")
    src.write_bytes(b"default bytes")
    default_dest = materialize_artifact(src, artifacts_dir, "default", "binary", "abc123")
    assert acme_dest != default_dest
    assert acme_dest.read_bytes() == b"acme bytes"
    assert default_dest.read_bytes() == b"default bytes"


def test_materialize_artifact_losing_a_rename_race_reuses_the_winners_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts_dir = tmp_path / "cache"
    dest_dir = local_artifact_dir(artifacts_dir, "default", "binary")
    dest_dir.mkdir(parents=True)
    winner_dest = dest_dir / "abc123"
    winner_dest.write_bytes(b"winner bytes")
    src = _written(tmp_path, "src.bin", b"loser bytes")

    real_rename = Path.rename

    def _rename(self: Path, target: object) -> Path:
        if self.parent == dest_dir and Path(str(target)) == winner_dest:
            raise OSError("simulated: the winner already landed here")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", _rename)
    dest = materialize_artifact(src, artifacts_dir, "default", "binary", "abc123")
    assert dest == winner_dest
    assert dest.read_bytes() == b"winner bytes"
    assert [p.name for p in dest_dir.iterdir()] == ["abc123"]
