"""Shared fixtures for the `bajutsu serve` test split (helpers / jobs / http).

Kept tiny and local so each split test file stays self-contained except for this one
project-layout builder, which every section needs (BE-0043: split so new serve tests add a
file instead of appending to a 1000-line monolith)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SCENARIO = "- name: alpha\n  steps:\n    - tap: { id: home.title }\n- name: beta\n  steps:\n    - tap: { id: x }\n"


def project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A scenarios dir + config + runs dir. `demo` declares its scenarios dir in config (so the
    config-driven listing works without a `--scenarios` override); `other` declares none."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(SCENARIO, encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [idb] }\napps:\n"
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
