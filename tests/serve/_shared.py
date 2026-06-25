"""Shared fixtures for the `bajutsu serve` test split (helpers / jobs / http).

Kept tiny and local so each split test file stays self-contained except for this one
project-layout builder, which every section needs (BE-0043: split so new serve tests add a
file instead of appending to a 1000-line monolith)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from bajutsu import serve as srv

SCENARIO = "- name: alpha\n  steps:\n    - tap: { id: home.title }\n- name: beta\n  steps:\n    - tap: { id: x }\n"


def project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A scenarios dir + config + runs dir. `demo` declares its scenarios dir in config (so the
    config-driven listing works without a `--scenarios` override); `other` declares none."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(SCENARIO, encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [idb] }\ntargets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn_dir} }}\n"
        "  other: { bundleId: com.example.other }\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    return scn_dir, cfg, runs


def write_run(runs: Path, run_id: str, *, ok: bool, scenarios: list[tuple[str, bool]]) -> None:
    """Write a minimal run dir (manifest.json + report.html) for the listing/HTTP tests."""
    d = runs / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "runId": run_id,
                "ok": ok,
                "scenarios": [{"scenario": n, "ok": o} for n, o in scenarios],
            }
        ),
        encoding="utf-8",
    )
    (d / "report.html").write_text("<html></html>", encoding="utf-8")


class FakeProc:
    """A stand-in for subprocess.Popen that yields canned stdout lines and a return code."""

    def __init__(self, lines: list[str], code: int = 0) -> None:
        self.stdout: Iterator[str] = iter(lines)
        self.returncode = code

    def wait(self) -> None:
        pass


def fake_popen(lines: list[str], code: int = 0):  # type: ignore[no-untyped-def]
    def popen(_cmd: list[str], **_kw: Any) -> FakeProc:
        return FakeProc(lines, code)

    return popen


def _serve(state: srv.ServeState):  # type: ignore[no-untyped-def]
    """Start the serve HTTP handler on an ephemeral port; return (server, port)."""
    server = srv.make_server(state, port=0)
    # serve_forever polls the shutdown flag every `poll_interval` (default 0.5s), so each test's
    # `server.shutdown()` in teardown blocked ~0.5s waiting for the loop to notice — and with ~70
    # http tests that fixed wait dominated the suite. A short interval makes shutdown near-instant;
    # request handling is unaffected (the interval is only the select() timeout between flag checks).
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    ).start()
    return server, server.server_address[1]


def _get(port: int, path: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return r.status, r.read(), r.headers.get("Content-Type", "")


def _get_json(port: int, path: str) -> Any:
    return json.loads(_get(port, path)[1])


def _post(port: int, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# Historic alias — `_post` already returns parsed JSON, so the two are identical.
_post_json = _post
