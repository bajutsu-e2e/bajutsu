"""Tests for the Replay-view codegen UI (BE-0137).

The "Generate code" export lives in the Author view (BE-0137 first slice) and the Replay view
(this slice): the same `POST /api/codegen` endpoint, surfaced next to the run output so a scenario
that just went green is one click from its native test. The HTML/JS is inlined, so these are
structural tests — assert the Replay markup and the shared wiring ship."""

from __future__ import annotations

from pathlib import Path

from _shared import _get, _serve, project

from bajutsu import serve as srv


def _index_text(tmp_path: Path) -> str:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        return _get(port, "/")[1].decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_replay_has_codegen_controls(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    for el in (
        'id="rp-emit"',
        'id="rp-codegen"',
        'id="rp-codegen-panel"',
        'id="rp-codegen-code"',
        'id="rp-codegen-copy"',
        'id="rp-codegen-download"',
    ):
        assert el in text, el


def test_replay_codegen_emit_select_has_accessible_name(tmp_path: Path) -> None:
    # The emit <select> sits in the run panel with no visible label, so it needs an ARIA name.
    text = _index_text(tmp_path)
    assert 'id="rp-emit"' in text
    assert 'aria-label="codegen destination"' in text


def test_both_views_share_one_codegen_wiring(tmp_path: Path) -> None:
    """Author and Replay both drive the one endpoint through the shared `makeCodegen` factory."""
    text = _index_text(tmp_path)
    assert "/api/codegen" in text
    assert "function makeCodegen(" in text
    assert "replayCodegen" in text
    assert "authorCodegen" in text
