"""Tests for the theme upload op + route (BE-0191 unit 6, part 2).

The upload op composes a canonical drop-in theme file from ``{name, kind, tokens}`` and writes it
into the ``--themes`` directory (unit 2), so an uploaded theme becomes a discoverable drop-in shared
across sessions. The server is the authority on the file format: it derives the ``[data-theme]`` id
from the (sanitized) name so the selector always matches the filename stem, guards the token
name/value so a malformed one can't break out of the rule, and invalidates the index cache so the
new theme lists on the next render.
"""

from __future__ import annotations

from pathlib import Path

from _shared import _get, _post, _serve, project

from bajutsu import serve as srv
from bajutsu.serve import themes
from bajutsu.serve.operations import theme_editor


def _state(tmp_path: Path, themes_dir: Path | None = None) -> srv.ServeState:
    scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, themes_dir=themes_dir
    )


def test_upload_theme_writes_discoverable_file(tmp_path: Path) -> None:
    """A successful upload writes ``<id>.css`` that the discovery layer then lists."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    payload, status = theme_editor.upload_theme(
        state,
        {"name": "Ocean Blue", "kind": "light", "tokens": {"--bg": "#012", "--acc": "#08f"}},
        None,
    )
    assert status == 200
    assert payload["ok"] is True
    assert payload["id"] == "ocean-blue"
    written = (themes_dir / "ocean-blue.css").read_text(encoding="utf-8")
    assert '[data-theme="ocean-blue"]' in written
    assert "--bg: #012;" in written
    assert "name: Ocean Blue" in written
    assert "kind: light" in written
    # It is a real drop-in now: the discovery layer registers it in the picker list.
    manifests = {m.id: m for m in themes.theme_manifests(themes_dir)}
    assert "ocean-blue" in manifests
    assert manifests["ocean-blue"].kind == "light"


def test_upload_theme_requires_themes_dir(tmp_path: Path) -> None:
    """Without ``--themes`` there is nowhere to persist a theme — a clear 400, not a crash."""
    state = _state(tmp_path, None)
    payload, status = theme_editor.upload_theme(
        state, {"name": "x", "tokens": {"--bg": "#000"}}, None
    )
    assert status == 400
    assert "--themes" in payload["error"]


def test_upload_theme_rejects_builtin_id(tmp_path: Path) -> None:
    """A name that slugs to a built-in id is refused (a drop-in must never shadow a built-in)."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    payload, status = theme_editor.upload_theme(
        state, {"name": "Midnight", "tokens": {"--bg": "#000"}}, None
    )
    assert status == 400
    assert "built-in" in payload["error"]
    assert not (themes_dir / "midnight.css").exists()


def test_upload_theme_rejects_empty_name(tmp_path: Path) -> None:
    """A name with no slug-able character can't become a filename/id — refused."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    _, status = theme_editor.upload_theme(state, {"name": "  ", "tokens": {"--bg": "#000"}}, None)
    assert status == 400


def test_upload_theme_rejects_unsafe_token(tmp_path: Path) -> None:
    """A token value carrying rule delimiters would break out of the block — refused, not dropped."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    _, status = theme_editor.upload_theme(
        state, {"name": "bad", "tokens": {"--bg": "red;} body{display:none"}}, None
    )
    assert status == 400
    assert not (themes_dir / "bad.css").exists()


def test_upload_theme_rejects_invalid_kind(tmp_path: Path) -> None:
    """An unrecognized kind is refused (fail-loudly), not silently coerced to a default."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    _, status = theme_editor.upload_theme(
        state, {"name": "weird", "kind": "neon", "tokens": {"--bg": "#000"}}, None
    )
    assert status == 400
    assert not (themes_dir / "weird.css").exists()


def test_upload_theme_defaults_kind_when_absent(tmp_path: Path) -> None:
    """An omitted kind defaults to dark — only an explicitly wrong value is rejected."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    _, status = theme_editor.upload_theme(
        state, {"name": "plain", "tokens": {"--bg": "#000"}}, None
    )
    assert status == 200
    assert "kind: dark" in (themes_dir / "plain.css").read_text(encoding="utf-8")


def test_upload_theme_rejects_non_ascii_token_name(tmp_path: Path) -> None:
    """A non-ASCII token name is refused, matching the client's ASCII-only guard (no `\\w` drift)."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    _, status = theme_editor.upload_theme(state, {"name": "uni", "tokens": {"--bĝ": "#000"}}, None)
    assert status == 400
    assert not (themes_dir / "uni.css").exists()


def test_upload_theme_name_cannot_smuggle_kind(tmp_path: Path) -> None:
    """A crafted name (CR + embedded `kind:`) cannot override the authoritative kind on re-parse."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    payload, status = theme_editor.upload_theme(
        state, {"name": "Evil\rkind: light", "kind": "dark", "tokens": {"--bg": "#000"}}, None
    )
    assert status == 200
    # Discovery re-parses the written manifest; the real kind (dark) must win, not the smuggled one.
    manifests = {m.id: m for m in themes.theme_manifests(themes_dir)}
    assert manifests[payload["id"]].kind == "dark"


def test_upload_theme_rejects_no_tokens(tmp_path: Path) -> None:
    """An empty token set would write an empty rule — refused."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    _, status = theme_editor.upload_theme(state, {"name": "empty", "tokens": {}}, None)
    assert status == 400


def test_upload_theme_overwrite_flag(tmp_path: Path) -> None:
    """Re-uploading the same name overwrites (operator intent) and reports it via ``overwritten``."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    body = {"name": "Dup", "tokens": {"--bg": "#111"}}
    p1, _ = theme_editor.upload_theme(state, body, None)
    assert p1["overwritten"] is False
    p2, _ = theme_editor.upload_theme(state, body, None)
    assert p2["overwritten"] is True


def test_api_theme_upload_route_and_cache_invalidation(tmp_path: Path) -> None:
    """POST /api/theme writes the file and the next index render lists the new theme (cache cleared)."""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    state = _state(tmp_path, themes_dir)
    server, port = _serve(state)
    try:
        # Prime the index cache with the themes dir empty (only the built-ins present).
        before = _get(port, "/")[1].decode()
        assert "Route Theme" not in before
        status, payload = _post(
            port, "/api/theme", {"name": "Route Theme", "kind": "dark", "tokens": {"--bg": "#010"}}
        )
        assert status == 200
        assert payload["id"] == "route-theme"
        assert (themes_dir / "route-theme.css").exists()
        # The upload invalidated the cached render, so the picker now offers the uploaded theme.
        after = _get(port, "/")[1].decode()
        assert "Route Theme" in after
    finally:
        server.shutdown()
        server.server_close()
