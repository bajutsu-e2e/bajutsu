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

import pytest

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
        "defaults: { backend: [ios] }\ntargets:\n"
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


class FakeGCSBucket:
    """A `google.cloud.storage.Bucket` stand-in with no real behavior — enough for `GCSObjectStore`
    to wrap, when a test only cares that a `gs://` URI resolved to a `GCSObjectStore`, not that it
    can read/write objects."""


class FakeGCSClient:
    """A `google.cloud.storage.Client` stand-in whose `bucket()` hands back a `FakeGCSBucket`, so
    `object_store_from_uri`'s ``gs://`` branch builds a real `GCSObjectStore` without a network call
    or real GCP credentials (BE-0204)."""

    def bucket(self, name: str) -> FakeGCSBucket:
        return FakeGCSBucket()


def patch_gcs_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patches `google.cloud.storage.Client` to `FakeGCSClient`, so a `gs://` URI resolves through
    the real `object_store_from_uri` without a network call or real GCP credentials (BE-0204)."""
    from google.cloud import storage

    monkeypatch.setattr(storage, "Client", FakeGCSClient)


class FakeObjectStore:
    """An in-memory `ObjectStore` (BE-0204) for the uploaded-bundle durable-storage tests (BE-0243):
    holds objects in a plain dict, and every method raises whatever `fail_with` is set to (if any) —
    used to exercise a store failure (read or write) without a real S3/GCS client."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(objects or {})
        self.put_calls: list[str] = []
        self.fail_with: Exception | None = None

    def exists(self, key: str) -> bool:
        if self.fail_with is not None:
            raise self.fail_with
        return key in self.objects

    def get_bytes(self, key: str) -> bytes | None:
        if self.fail_with is not None:
            raise self.fail_with
        return self.objects.get(key)

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.put_calls.append(key)
        self.objects[key] = data

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.put_calls.append(key)
        self.objects[key] = path.read_bytes()
