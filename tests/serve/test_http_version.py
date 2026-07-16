"""HTTP wiring for the server-identity endpoints (BE-0272): a real ThreadingHTTPServer.

The checkout read is pinned to a non-Git `tmp_path` so the JSON shape is deterministic regardless
of the ambient checkout; the Git-present behaviour is covered at the operations level.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _shared import _get_json, _serve

from bajutsu import __version__
from bajutsu import serve as srv
from bajutsu.serve.operations import version as version_ops


def test_http_version_reports_the_running_version(tmp_path: Path) -> None:
    server, port = _serve(srv.ServeState(runs_dir=tmp_path, cwd=tmp_path))
    try:
        assert _get_json(port, "/api/version") == {"version": __version__}
    finally:
        server.shutdown()
        server.server_close()


def test_http_version_checkout_returns_the_identity_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(version_ops, "_REPO_ANCHOR", tmp_path)  # no .git → all-null, no ambient dep
    monkeypatch.delenv("BAJUTSU_BUILD_COMMIT", raising=False)  # and no build-arg fallback (BE-0277)
    server, port = _serve(srv.ServeState(runs_dir=tmp_path, cwd=tmp_path))
    try:
        assert _get_json(port, "/api/version/checkout") == {
            "commit": None,
            "branch": None,
            "dirty": False,
            "source": None,
        }
    finally:
        server.shutdown()
        server.server_close()
