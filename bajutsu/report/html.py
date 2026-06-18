"""Jinja rendering: turn RunResults into a pure-data context and render the self-contained
report.html (templates live in bajutsu/templates/)."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from bajutsu.orchestrator import RunResult
from bajutsu.report.format import _fmt_duration
from bajutsu.report.manifest import _run_backend, junit_xml, manifest_dict
from bajutsu.report.panels import _scenario_data

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


# --- Jinja rendering ---


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


@functools.lru_cache(maxsize=2)
def _asset(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def html_report(
    run_id: str,
    results: list[RunResult],
    run_dir: Path | None = None,
    definitions: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
    source_name: str | None = None,
    description: str | None = None,
) -> str:
    """A self-contained interactive HTML report (inline CSS + JS, no external assets).

    When `run_dir` is given the captured logs/traces are embedded inline (so the report
    works opened directly from disk); otherwise only the structure renders.
    `definitions` (structured) and `sources` (raw YAML), both aligned with `results`,
    drive the merged Result tab and its Rich/YAML toggle.
    """
    passed = sum(1 for r in results if r.ok)
    scenarios = [
        _scenario_data(
            r,
            run_dir,
            definitions[i] if definitions and i < len(definitions) else None,
            sources[i] if sources and i < len(sources) else None,
        )
        for i, r in enumerate(results)
    ]
    devices = dict.fromkeys(r.device for r in results if r.device)  # ordered-unique device count
    total_duration = _fmt_duration(sum(r.duration_s for r in results))
    return (
        _env()
        .get_template("report.html.j2")
        .render(
            run_id=run_id,
            passed=passed,
            failed=len(results) - passed,
            overall=passed == len(results),
            backend=_run_backend(results),
            device_count=len(devices),
            total_duration=total_duration,
            css=_asset("report.css"),
            js=_asset("report.js"),
            scenarios=scenarios,
            source_name=source_name,
            description=description,
        )
    )


def write_report(
    run_dir: Path,
    run_id: str,
    results: list[RunResult],
    definitions: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
    source_name: str | None = None,
    description: str | None = None,
) -> Path:
    """Write manifest.json, junit.xml, and report.html under run_dir; return the manifest path.

    `definitions` (structured) and `sources` (raw YAML), aligned with `results`, feed
    the report's merged Result tab and its Rich/YAML toggle.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_dict(run_id, results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "junit.xml").write_text(junit_xml(results), encoding="utf-8")
    (run_dir / "report.html").write_text(
        html_report(run_id, results, run_dir, definitions, sources, source_name, description),
        encoding="utf-8",
    )
    return manifest_path
