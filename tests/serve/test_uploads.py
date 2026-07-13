"""Tests for the uploaded-bundle extractor (bajutsu/serve/uploads.py, BE-0073).

The extractor materializes an uploaded zip into a sandbox, rejecting zip-slip (absolute paths,
``..`` traversal, symlink entries) and zip-bombs (too many entries, too much uncompressed data, an
absurd per-entry compression ratio) — the security-sensitive half of the upload path. Pure
packaging: no device, no AI, runs on the Linux gate against fixture zips.
"""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from bajutsu.serve import uploads
from bajutsu.serve.uploads import (
    BundleError,
    extract_bundle,
    find_bundle_config,
    materialize_bundle,
)


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _written(tmp_path: Path, blob: bytes) -> Path:
    p = tmp_path / "bundle.zip"
    p.write_bytes(blob)
    return p


def _dest(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    return out


def test_extracts_a_valid_bundle(tmp_path: Path) -> None:
    blob = _zip(
        {
            "bajutsu.config.yaml": b"targets: {}\n",
            "scenarios/smoke.yaml": b"- name: a\n  steps: []\n",
            "build/App.app/Info.plist": b"<plist/>",
        }
    )
    dest = _dest(tmp_path)
    extract_bundle(_written(tmp_path, blob), dest)
    assert (dest / "bajutsu.config.yaml").read_bytes() == b"targets: {}\n"
    assert (dest / "scenarios" / "smoke.yaml").is_file()
    assert (dest / "build" / "App.app" / "Info.plist").is_file()  # nested dirs are created


def test_rejects_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="unsafe entry"):
        extract_bundle(_written(tmp_path, _zip({"/etc/evil": b"x"})), _dest(tmp_path))


def test_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match=r"unsafe entry|escapes"):
        extract_bundle(_written(tmp_path, _zip({"../escape.txt": b"x"})), _dest(tmp_path))
    assert not (tmp_path / "escape.txt").exists()  # nothing written outside the sandbox


def test_rejects_symlink_entry(tmp_path: Path) -> None:
    # A symlink entry could point outside the sandbox, so it's rejected before anything is written.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, b"/etc/passwd")
    dest = _dest(tmp_path)
    with pytest.raises(BundleError, match="symlink"):
        extract_bundle(_written(tmp_path, buf.getvalue()), dest)
    assert not (dest / "link").exists()


def test_rejects_too_many_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads, "MAX_ENTRIES", 2)
    blob = _zip({"a": b"1", "b": b"2", "c": b"3"})
    with pytest.raises(BundleError, match="too many entries"):
        extract_bundle(_written(tmp_path, blob), _dest(tmp_path))


def test_rejects_total_uncompressed_over_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The streamed byte count is the real defense — it stops the moment the cap is crossed.
    monkeypatch.setattr(uploads, "MAX_TOTAL_BYTES", 8)
    with pytest.raises(BundleError, match="uncompressed"):
        extract_bundle(_written(tmp_path, _zip({"big.txt": b"x" * 64})), _dest(tmp_path))


def test_rejects_compression_ratio_bomb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads, "MAX_RATIO", 5)
    # A highly compressible payload above the ratio floor is flagged from the header, pre-stream.
    with pytest.raises(BundleError, match="ratio"):
        extract_bundle(_written(tmp_path, _zip({"bomb.txt": b"\x00" * 1_000_000})), _dest(tmp_path))


def test_rejects_a_non_zip(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="valid zip"):
        extract_bundle(_written(tmp_path, b"not a zip at all"), _dest(tmp_path))


def test_malformed_file_then_subpath_is_a_bundle_error(tmp_path: Path) -> None:
    # A file entry, then a path *under* it: mkdir/open raises a bare OSError — surface it as a
    # BundleError (a bad bundle → 400 + cleanup), never an uncaught 500. Entry order is load-bearing
    # (the file must precede the path under it), and _zip preserves insertion order.
    blob = _zip({"src": b"i am a file", "src/main.py": b"print()"})
    with pytest.raises(BundleError, match="could not extract"):
        extract_bundle(_written(tmp_path, blob), _dest(tmp_path))


def test_corrupt_member_is_a_bundle_error(tmp_path: Path) -> None:
    # A corrupt member raises zipfile.BadZipFile *during the read* (not at open) — it is neither
    # OSError nor BundleError, so it must be caught in the stream loop, else it drops the connection.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("data.bin", b"A" * 64)
    raw = bytearray(buf.getvalue())
    raw[raw.index(b"A" * 64)] ^= 0xFF  # flip a stored byte → CRC mismatch on read
    with pytest.raises(BundleError, match="could not extract"):
        extract_bundle(_written(tmp_path, bytes(raw)), _dest(tmp_path))


def test_find_bundle_config_at_root(tmp_path: Path) -> None:
    (tmp_path / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")
    assert find_bundle_config(tmp_path) == tmp_path / "bajutsu.config.yaml"


def test_find_bundle_config_one_level_down(tmp_path: Path) -> None:
    # A zip made from a folder nests everything under one dir; the config is found a level down.
    sub = tmp_path / "my-suite"
    sub.mkdir()
    (sub / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")
    assert find_bundle_config(tmp_path) == sub / "bajutsu.config.yaml"


def test_find_bundle_config_one_level_down_ignores_macos_cruft(tmp_path: Path) -> None:
    # macOS Archive Utility adds a `__MACOSX/` sibling; it must not hide the real top folder.
    (tmp_path / "__MACOSX").mkdir()
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "my-suite"
    sub.mkdir()
    (sub / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")
    assert find_bundle_config(tmp_path) == sub / "bajutsu.config.yaml"


def test_find_bundle_config_in_underscore_named_folder(tmp_path: Path) -> None:
    # Only the known cruft (`__MACOSX/`, dot-dirs) is skipped — a real top folder that merely starts
    # with `__` must still be found, not mistaken for cruft and dropped.
    sub = tmp_path / "__suite"
    sub.mkdir()
    (sub / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")
    assert find_bundle_config(tmp_path) == sub / "bajutsu.config.yaml"


def test_find_bundle_config_absent(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("hi", encoding="utf-8")
    assert find_bundle_config(tmp_path) is None


# ---- materialize_bundle (BE-0243) -----------------------------------------------------------


def _valid_blob() -> bytes:
    return _zip({"bajutsu.config.yaml": b"targets: {}\n"})


def test_materialize_bundle_extracts_on_a_miss(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "cache"
    zip_path = _written(tmp_path, _valid_blob())
    dest = materialize_bundle(zip_path, uploads_dir, "abc123")
    assert dest == uploads_dir / "abc123"
    assert (dest / "bajutsu.config.yaml").is_file()
    # No leftover temp dir beside the resolved entry.
    assert [p.name for p in uploads_dir.iterdir()] == ["abc123"]


def test_materialize_bundle_reuses_an_existing_entry(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "cache"
    zip_path = _written(tmp_path, _valid_blob())
    materialize_bundle(zip_path, uploads_dir, "abc123")
    zip_path.unlink()  # a cache hit must not need the zip again
    dest = materialize_bundle(zip_path, uploads_dir, "abc123")
    assert dest == uploads_dir / "abc123"
    assert (dest / "bajutsu.config.yaml").is_file()


def test_materialize_bundle_runs_validate_only_on_a_miss(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "cache"
    zip_path = _written(tmp_path, _valid_blob())
    calls: list[Path] = []
    materialize_bundle(zip_path, uploads_dir, "abc123", validate=calls.append)
    assert len(calls) == 1
    materialize_bundle(zip_path, uploads_dir, "abc123", validate=calls.append)
    assert len(calls) == 1  # the second call cache-hit and never re-validated


def test_materialize_bundle_validate_failure_leaves_no_cache_entry(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "cache"
    zip_path = _written(tmp_path, _valid_blob())

    def _reject(_root: Path) -> None:
        raise ValueError("no good")

    with pytest.raises(ValueError, match="no good"):
        materialize_bundle(zip_path, uploads_dir, "abc123", validate=_reject)
    # Neither the keyed entry nor a leftover temp dir survives a validation failure.
    assert not (uploads_dir / "abc123").exists()
    assert list(uploads_dir.iterdir()) == []


def test_materialize_bundle_extract_failure_leaves_no_cache_entry(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "cache"
    zip_path = _written(tmp_path, b"not a zip at all")
    with pytest.raises(BundleError):
        materialize_bundle(zip_path, uploads_dir, "abc123")
    assert list(uploads_dir.iterdir()) == []


def test_materialize_bundle_losing_a_rename_race_reuses_the_winners_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulates two concurrent misses for the same sha256: this call's own tmp.rename(dest) fails
    # because a concurrent call already won and landed its (byte-identical) tree at dest first.
    uploads_dir = tmp_path / "cache"
    uploads_dir.mkdir()
    winner_dest = uploads_dir / "abc123"
    winner_dest.mkdir()
    (winner_dest / "bajutsu.config.yaml").write_text("targets: {}\n", encoding="utf-8")
    zip_path = _written(tmp_path, _valid_blob())

    real_rename = Path.rename

    def _rename(self: Path, target: object) -> Path:
        if self.parent == uploads_dir and Path(str(target)) == winner_dest:
            raise OSError("simulated: the winner already landed here")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", _rename)
    dest = materialize_bundle(zip_path, uploads_dir, "abc123")
    assert dest == winner_dest
    # Only the winner's directory remains — this call's own tmp dir was discarded, not left behind.
    assert [p.name for p in uploads_dir.iterdir()] == ["abc123"]


def test_materialize_bundle_a_genuine_rename_failure_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A rename failure that is NOT a lost race (dest never appears) must not be swallowed as if it
    # were one — a real filesystem error (permissions, disk full) must still surface.
    uploads_dir = tmp_path / "cache"
    uploads_dir.mkdir()
    zip_path = _written(tmp_path, _valid_blob())

    def _rename(self: Path, target: object) -> Path:
        raise OSError("simulated: permission denied")

    monkeypatch.setattr(Path, "rename", _rename)
    with pytest.raises(OSError, match="permission denied"):
        materialize_bundle(zip_path, uploads_dir, "abc123")
    # Neither a winner's entry nor a leftover temp dir exists — the raise propagated cleanly.
    assert list(uploads_dir.iterdir()) == []
