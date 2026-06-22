"""Tests for the run-directory archiver (bajutsu/report/archive.py, BE-0060).

The archiver bundles a finished run's whole directory into a single zip rooted under a
`<id>/` folder, so `report.html`'s relative asset links resolve offline. It is pure packaging —
no device, no AI — and reaches strictly inside the run dir.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from bajutsu.report.archive import archive_run_dir, zip_tree


def _names(blob: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return zf.namelist()


def _read(blob: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return zf.read(name)


def _make_run(tmp_path: Path) -> Path:
    run = tmp_path / "runs" / "20260101-120000"
    (run / "demo").mkdir(parents=True)
    (run / "report.html").write_text("<img src='demo/shot.png'>", encoding="utf-8")
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    (run / "demo" / "shot.png").write_bytes(b"\x89PNG")
    return run


def test_zip_tree_is_deterministic_and_sorted() -> None:
    a = zip_tree([("b.txt", b"B"), ("a.txt", b"A")])
    b = zip_tree([("a.txt", b"A"), ("b.txt", b"B")])
    assert a == b  # byte-identical regardless of input order
    assert _names(a) == ["a.txt", "b.txt"]  # sorted


def test_archive_roots_everything_under_the_run_id_folder(tmp_path: Path) -> None:
    blob = archive_run_dir(_make_run(tmp_path))
    names = _names(blob)
    assert "20260101-120000/report.html" in names
    assert "20260101-120000/manifest.json" in names
    assert "20260101-120000/demo/shot.png" in names
    # every entry is under the single <id>/ root, so relative links resolve on unzip
    assert all(n.startswith("20260101-120000/") for n in names)


def test_archive_preserves_content(tmp_path: Path) -> None:
    blob = archive_run_dir(_make_run(tmp_path))
    assert _read(blob, "20260101-120000/demo/shot.png") == b"\x89PNG"
    assert b"demo/shot.png" in _read(blob, "20260101-120000/report.html")


def test_archive_reaches_strictly_inside_the_run_dir(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    # a sibling secret next to runs/ must never be pulled in
    (tmp_path / ".env").write_text("SECRET=hunter2", encoding="utf-8")
    (run.parent / "other-run").mkdir()
    (run.parent / "other-run" / "leak.txt").write_text("nope", encoding="utf-8")
    names = _names(archive_run_dir(run))
    assert not any(".env" in n or "other-run" in n or "leak" in n for n in names)


def test_archive_is_reproducible(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    assert archive_run_dir(run) == archive_run_dir(run)  # same dir -> same bytes


def test_archive_skips_a_symlink_that_escapes_the_run_dir(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET=hunter2", encoding="utf-8")
    (run / "leak.txt").symlink_to(secret)  # a symlink in the run dir pointing outside it
    blob = archive_run_dir(run)
    names = _names(blob)
    assert "20260101-120000/leak.txt" not in names  # the symlink (and its target) is excluded
    assert all(b"hunter2" not in _read(blob, n) for n in names)
