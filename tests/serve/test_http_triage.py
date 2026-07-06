"""End-to-end test for the serve triage endpoint (BE-0147).

Real `ThreadingHTTPServer` + a real `bajutsu triage` subprocess: the heuristic agent is
deterministic and needs no Simulator or LLM, so this exercises the whole loop — POST /api/triage
spawns the job, the job writes `triage.json` into the run dir, and the UI reads it back — without a
single mock.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from _shared import _get, _get_json, _post, _serve, project

from bajutsu.serve.jobs import ServeState


def _write_failed_run(runs: Path, run_id: str) -> Path:
    """A minimal failed run: `alpha` tapped `home.titel`, but the screen only has `home.title`."""
    run = runs / run_id
    (run / "00-alpha" / "step0").mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "runId": run_id,
                "ok": False,
                "scenarios": [
                    {
                        "scenario": "alpha",
                        "ok": False,
                        "steps": [
                            {
                                "index": 0,
                                "action": "tap",
                                "ok": False,
                                "reason": "一致なし: {'id': 'home.titel'}",
                                "artifacts": [
                                    {"name": "00-alpha/step0/elements.json", "kind": "elements"}
                                ],
                            }
                        ],
                        "expect_results": [],
                        "failure": "step0 tap: 一致なし",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run / "scenario.yaml").write_text(
        "- name: alpha\n  steps:\n    - tap: { id: home.titel }\n", encoding="utf-8"
    )
    (run / "00-alpha" / "step0" / "elements.json").write_text(
        json.dumps(
            [
                {
                    "identifier": "home.title",
                    "label": "Home",
                    "traits": ["button"],
                    "value": None,
                    "frame": [0, 0, 10, 10],
                }
            ]
        ),
        encoding="utf-8",
    )
    return run


def test_http_triage_diagnoses_and_serves_the_result(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    # The scenario source carries the same typo the run failed on, so the heuristic proposes a
    # rename fix and the diff/patched preview is non-empty.
    (scn_dir / "smoke.yaml").write_text(
        "- name: alpha\n  steps:\n    - tap: { id: home.titel }\n", encoding="utf-8"
    )
    run = _write_failed_run(runs, "20260101-000000")

    server, port = _serve(
        ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, resp = _post(
            port, "/api/triage", {"runId": run.name, "target": "demo", "scenario": "smoke.yaml"}
        )
        assert status == 200 and resp["jobId"]
        for _ in range(500):
            job = _get_json(port, "/api/jobs/" + resp["jobId"])
            if job["status"] == "done":
                break
            time.sleep(0.02)
        assert job["status"] == "done" and job["ok"] is True

        # The machine-readable result is written into the run dir and served back for the UI.
        code, raw, _ = _get(port, "/runs/" + run.name + "/triage.json")
        assert code == 200
        data = json.loads(raw)
        assert data["category"] == "selector"
        assert data["fix"]["kind"] == "renameId"
        assert (data["fix"]["find"], data["fix"]["replace"]) == ("home.titel", "home.title")
        # The diff preview + patched text the Apply button writes back through POST /api/scenario.
        assert data["apply"]["count"] == 1
        assert "home.title }" in data["apply"]["patched"]
        assert "home.titel" in data["apply"]["diff"]
        # Dry-run only: the job never wrote the source file (apply is the human's explicit action).
        assert "home.titel" in (scn_dir / "smoke.yaml").read_text(encoding="utf-8")
    finally:
        server.shutdown()
