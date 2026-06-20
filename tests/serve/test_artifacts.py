"""Tests for the ArtifactStore seam (BE-0015 local/server parity, PR3).

`ArtifactStore` is the one point where run-artifact reads diverge between local and server
hosting: the local store reads files confined to `runs_dir` (`LocalArtifactStore`), while a server
store would fetch from object storage or hand back a signed-URL redirect. The path-containment
that keeps a crafted `rel` from escaping `runs_dir` lives in one place here.
"""

from __future__ import annotations

from pathlib import Path

from _shared import write_run

from bajutsu import serve as srv


def _runs(tmp_path: Path) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    write_run(runs, "20260610-1", ok=True, scenarios=[("smoke", True)])
    return runs


def test_get_returns_body_and_content_type(tmp_path: Path) -> None:
    store = srv.LocalArtifactStore(_runs(tmp_path))
    art = store.get("20260610-1/report.html")
    assert art is not None
    assert art.body == b"<html></html>"
    assert "text/html" in art.content_type
    assert art.redirect is None  # local never redirects


def test_get_guesses_png_content_type(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    (runs / "20260610-1" / "shot.png").write_bytes(b"PNGDATA")
    art = srv.LocalArtifactStore(runs).get("20260610-1/shot.png")
    assert art is not None and art.content_type == "image/png" and art.body == b"PNGDATA"


def test_open_bytes_reads_confined_file(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    (runs / "20260610-1" / "sid0").mkdir()
    (runs / "20260610-1" / "sid0" / "visual-actual.png").write_bytes(b"SHOT")
    store = srv.LocalArtifactStore(runs)
    assert store.open_bytes("20260610-1/sid0/visual-actual.png") == b"SHOT"


def test_escaping_or_missing_paths_are_none(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    store = srv.LocalArtifactStore(runs)
    for rel in ("../secret.txt", str(secret), "", "20260610-1", "20260610-1/missing.png"):
        # escapes / empty / a directory / a missing file all resolve to nothing
        assert store.get(rel) is None, rel
        assert store.open_bytes(rel) is None, rel


def test_list_runs_summarizes_newest_first(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    write_run(runs, "20260612-9", ok=False, scenarios=[("a", True), ("b", False)])
    listed = srv.LocalArtifactStore(runs).list_runs()
    assert [r["id"] for r in listed] == ["20260612-9", "20260610-1"]  # reverse-lexicographic
    assert listed[0]["passed"] == 1 and listed[0]["total"] == 2 and listed[0]["ok"] is False
