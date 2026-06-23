"""manifest.json (the run's single source of truth) and JUnit XML."""

from __future__ import annotations

from dataclasses import asdict
from xml.etree import ElementTree as ET

from bajutsu.orchestrator import RunResult


def _run_backend(results: list[RunResult]) -> str:
    """The actuator that drove the run. One actuator is fixed per run, so this is
    normally a single name; if scenarios somehow differ, they are joined."""
    names = dict.fromkeys(r.backend for r in results if r.backend)  # ordered-unique
    return ", ".join(names)


# The render model's version. Bump when a field the report needs is added, so an older run can be
# detected and its newer-only sections shown as "not captured" rather than failing (BE-0068).
SCHEMA_VERSION = 1


def manifest_dict(
    run_id: str, results: list[RunResult], *, source_name: str | None = None
) -> dict[str, object]:
    """Build the manifest — the run's canonical, versioned render model (BE-0068). RunResult and
    its parts are dataclasses, so asdict() captures step/expect outcomes verbatim. `backend` is the
    actuator that drove the run (each scenario also carries its own `backend`); `sourceName` is the
    label the report's YAML toggle shows, persisted here so a re-render can recover it."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id,
        "ok": all(r.ok for r in results),
        "backend": _run_backend(results),
        "sourceName": source_name,
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
    suite = ET.Element("testsuite", name="bajutsu", tests=str(len(results)), failures=str(failures))
    for r in results:
        case = ET.SubElement(suite, "testcase", name=r.scenario, classname="bajutsu")
        if not r.ok:
            failure = ET.SubElement(case, "failure", message=r.failure or "failed")
            failure.text = _details(r)
    return ET.tostring(suite, encoding="unicode")
