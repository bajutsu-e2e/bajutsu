"""Reporting — manifest.json (the single source of truth) and JUnit XML.

A run executes one or more scenarios (list[RunResult]). The manifest records the
step/expect outcomes per scenario; JUnit feeds CI. Evidence artifacts are added
later (the evidence subsystem); the manifest already has a place for them.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET

from simpilot.orchestrator import RunResult


def manifest_dict(run_id: str, results: list[RunResult]) -> dict[str, object]:
    """Build the manifest. RunResult and its parts are dataclasses, so asdict()
    captures step/expect outcomes verbatim."""
    return {
        "runId": run_id,
        "ok": all(r.ok for r in results),
        "scenarios": [asdict(r) for r in results],
    }


def _details(r: RunResult) -> str:
    lines: list[str] = []
    for s in r.steps:
        status = "ok" if s.ok else "FAIL"
        lines.append(f"step {s.index} {s.action}: {status} {s.reason}".rstrip())
    for a in r.expect_results:
        status = "ok" if a.ok else "FAIL"
        lines.append(f"expect {a.kind}: {status} {a.reason}".rstrip())
    return "\n".join(lines)


def junit_xml(results: list[RunResult]) -> str:
    """One testcase per scenario; a failing scenario gets a <failure>."""
    failures = sum(0 if r.ok else 1 for r in results)
    suite = ET.Element(
        "testsuite",
        name="simpilot",
        tests=str(len(results)),
        failures=str(failures),
    )
    for r in results:
        case = ET.SubElement(suite, "testcase", name=r.scenario, classname="simpilot")
        if not r.ok:
            failure = ET.SubElement(case, "failure", message=r.failure or "failed")
            failure.text = _details(r)
    return ET.tostring(suite, encoding="unicode")


def write_report(run_dir: Path, run_id: str, results: list[RunResult]) -> Path:
    """Write manifest.json and junit.xml under run_dir; return the manifest path."""
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_dict(run_id, results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "junit.xml").write_text(junit_xml(results), encoding="utf-8")
    return manifest_path
