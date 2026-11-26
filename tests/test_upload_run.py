"""Tests for scripts/upload_run.py — uploading a finished run to a serve (BE-0510)."""

from __future__ import annotations

import ast
import io
import json
import os
import sys
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import scripts.upload_run as upload_run
from scripts.upload_run import main

_RUN_ID = "20261126-101500"


class _Server(ThreadingHTTPServer):
    """Records each request, and answers with whatever the test set."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        self.status = 200
        self.reply = json.dumps(
            {"runId": _RUN_ID, "url": f"/runs/{_RUN_ID}/report.html", "archive": "/a.zip"}
        )
        self.location: str | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def _answer(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.requests.append((self.command, self.path, dict(self.headers), body))
        self.send_response(self.server.status)
        if self.server.location is not None:
            self.send_header("Location", self.server.location)
        self.send_header("Content-Length", str(len(self.server.reply.encode())))
        self.end_headers()
        self.wfile.write(self.server.reply.encode())

    do_GET = do_POST = _answer  # noqa: N815  the names `http.server` dispatches to

    def log_message(self, format: str, *args: object) -> None:
        pass


def _running() -> Iterator[_Server]:
    server = _Server()
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}).start()
    yield server
    server.shutdown()
    server.server_close()


server = pytest.fixture(name="server")(_running)
elsewhere = pytest.fixture(name="elsewhere")(_running)


@pytest.fixture(autouse=True)
def _no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAJUTSU_SERVE_KEY", raising=False)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "runs" / _RUN_ID
    (run / "00-a").mkdir(parents=True)
    (run / "manifest.json").write_text('{"ok": false, "scenarios": []}')
    (run / "00-a" / "after.png").write_bytes(b"png")
    (tmp_path / "secret.txt").write_text("outside the run")
    (run / "link.txt").symlink_to(tmp_path / "secret.txt")
    return run


def test_it_posts_the_zip_and_prints_the_two_urls(
    server: _Server, run_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The run failed, and the exit status still says only that the import succeeded.
    assert main([str(run_dir), "--server", f"{server.url}/"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert out == [f"{server.url}/runs/{_RUN_ID}/report.html", f"{server.url}/a.zip"]
    ((method, path, headers, body),) = server.requests
    assert (method, path) == ("POST", "/api/runs/import")
    assert headers["Content-Type"] == "application/zip"
    assert "Authorization" not in headers
    names = zipfile.ZipFile(io.BytesIO(body)).namelist()
    assert names == [f"{_RUN_ID}/00-a/after.png", f"{_RUN_ID}/manifest.json"]


def test_a_run_id_is_looked_up_under_the_runs_directory(server: _Server, run_dir: Path) -> None:
    assert main([_RUN_ID, "--server", server.url, "--runs-dir", str(run_dir.parent)]) == 0
    assert len(server.requests) == 1


@pytest.mark.parametrize("source", ["environment", "file"])
def test_the_key_is_sent_as_a_bearer_token(
    server: _Server,
    run_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    args = [str(run_dir), "--server", server.url]
    if source == "environment":
        monkeypatch.setenv("BAJUTSU_SERVE_KEY", "s3cret")
    else:
        (tmp_path / "key").write_text("s3cret\n")
        args += ["--key-file", str(tmp_path / "key")]

    assert main(args) == 0

    assert server.requests[0][2]["Authorization"] == "Bearer s3cret"


def test_the_key_is_never_taken_as_an_argument(run_dir: Path) -> None:
    with pytest.raises(SystemExit):
        main([str(run_dir), "--server", "https://example.com", "--key", "s3cret"])


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_is_not_followed_and_never_carries_the_key_away(
    server: _Server,
    elsewhere: _Server,
    run_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    monkeypatch.setenv("BAJUTSU_SERVE_KEY", "s3cret")
    server.status, server.location = status, f"{elsewhere.url}/api/runs/import"

    assert main([str(run_dir), "--server", server.url]) == 1

    assert elsewhere.requests == []


@pytest.mark.parametrize(
    "address", ["http://example.com", "http://192.0.2.1:8765", "ftp://localhost", "localhost:8765"]
)
def test_a_cleartext_or_malformed_server_is_refused_before_anything_is_sent(
    run_dir: Path, capsys: pytest.CaptureFixture[str], address: str
) -> None:
    assert main([str(run_dir), "--server", address]) == 1
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "address",
    ["http://localhost:8765", "http://127.0.0.1", "http://[::1]:1", "https://example.com"],
)
def test_a_loopback_http_or_any_https_server_is_accepted(address: str) -> None:
    assert upload_run.checked_server(address) == address


def test_dry_run_lists_the_files_and_sends_nothing(
    server: _Server, run_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(run_dir), "--server", server.url, "--dry-run"]) == 0

    assert server.requests == []
    assert f"{_RUN_ID}/00-a/after.png" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("status", "reply", "message"),
    [
        (409, '{"error": "a run with this id already exists"}', "409: a run with this id"),
        (502, "<html>bad gateway</html>", "502"),
        (200, "<html>a login page</html>", "not JSON"),
        (200, "{}", "names no run URL"),
    ],
)
def test_a_refusal_or_an_unexpected_reply_fails(
    server: _Server,
    run_dir: Path,
    capsys: pytest.CaptureFixture[str],
    status: int,
    reply: str,
    message: str,
) -> None:
    server.status, server.reply = status, reply

    assert main([str(run_dir), "--server", server.url]) == 1

    assert message in capsys.readouterr().err


def test_the_dropped_files_are_named_on_stderr(
    server: _Server, run_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server.reply = json.dumps({"url": "/u", "archive": "/a", "dropped": ["x/hierarchy.raw.xml"]})

    assert main([str(run_dir), "--server", server.url]) == 0

    assert "hierarchy.raw.xml" in capsys.readouterr().err


def test_a_directory_that_holds_no_finished_run_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(tmp_path), "--server", "https://example.com"]) == 1
    assert "manifest.json" in capsys.readouterr().err


def test_an_empty_key_file_fails(run_dir: Path, tmp_path: Path) -> None:
    (tmp_path / "key").write_text("\n")

    argv = [str(run_dir), "--server", "https://example.com", "--key-file", str(tmp_path / "key")]

    assert main(argv) == 1


def test_the_script_imports_the_standard_library_alone() -> None:
    tree = ast.parse(Path(upload_run.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(str(node.module).split(".")[0])

    assert imported <= sys.stdlib_module_names


def test_a_loopback_upload_ignores_an_environment_proxy(
    server: _Server, elsewhere: _Server, run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAJUTSU_SERVE_KEY", "s3cret")
    for name in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(name, elsewhere.url)
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)

    assert main([str(run_dir), "--server", server.url]) == 0

    assert len(server.requests) == 1
    assert elsewhere.requests == []


@pytest.mark.parametrize("key", [b"two\nlines", "snowman \u2603".encode(), b"\xff\xfe"])
def test_a_key_no_header_can_carry_is_refused_without_echoing_it(
    run_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], key: bytes
) -> None:
    (tmp_path / "key").write_bytes(key)

    argv = [str(run_dir), "--server", "https://example.com", "--key-file", str(tmp_path / "key")]

    assert main(argv) == 1
    err = capsys.readouterr().err
    assert "key" in err
    assert "two" not in err


def test_a_skipped_symlink_is_named(run_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(run_dir), "--server", "https://example.com", "--dry-run"]) == 0

    assert "skipping the symlink" in capsys.readouterr().err


def test_a_file_dated_before_1980_is_still_sent(server: _Server, run_dir: Path) -> None:
    os.utime(run_dir / "00-a" / "after.png", (0, 0))

    assert main([str(run_dir), "--server", server.url]) == 0
