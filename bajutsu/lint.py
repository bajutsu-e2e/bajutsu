"""Scenario linter — validate YAML scenarios without running them.

Reuses the existing Pydantic schema validation (``load_scenario_file``).
Returns a list of human-readable error strings (empty = valid).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from bajutsu.scenario import Scenario, load_scenario_file


def lint_text(text: str) -> list[str]:
    """Validate scenario YAML text. Returns a list of error messages (empty = ok)."""
    try:
        load_scenario_file(text)
    except (ValidationError, ValueError) as e:
        return _format_validation_error(e)
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"]
    return []


def lint_file(path: Path) -> list[str]:
    """Validate a scenario file on disk."""
    if not path.exists():
        return [f"file not found: {path}"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"read error: {e}"]
    return lint_text(text)


def provenance_coverage(scenarios: list[Scenario]) -> str | None:
    """An advisory line on how many top-level steps carry `from:` provenance (BE-0044), or None
    when there are no steps to report on. Never an error: a hand-authored scenario legitimately
    carries none, so this mirrors `doctor`'s advisory style rather than failing the lint."""
    steps = [step for s in scenarios for step in s.steps]
    if not steps:
        return None
    # A non-empty phrase counts as provenance — matching the writer (`_provenance`), which omits an
    # empty `from:` rather than emitting one, so an empty string never reads as "covered".
    with_from = sum(1 for step in steps if step.from_)
    return f"provenance: {with_from}/{len(steps)} step(s) carry `from:`"


def scenario_json_schema() -> str:
    """Return the JSON Schema for a scenario file.

    Covers both on-disk forms: a bare list of scenarios, or a
    ``{description, scenarios}`` mapping."""
    import json

    from bajutsu.scenario import Scenario, ScenarioFile

    file_schema = ScenarioFile.model_json_schema()
    list_schema = {"type": "array", "items": {"$ref": "#/$defs/Scenario"}}
    defs = file_schema.pop("$defs", {})
    # Ensure Scenario def is present (it may be nested under ScenarioFile's defs).
    if "Scenario" not in defs:
        scenario_schema = Scenario.model_json_schema()
        defs.update(scenario_schema.pop("$defs", {}))
        defs["Scenario"] = {k: v for k, v in scenario_schema.items() if k != "$defs"}

    return json.dumps(
        {"anyOf": [list_schema, file_schema], "$defs": defs},
        indent=2,
        ensure_ascii=False,
    )


def _format_validation_error(e: ValidationError | ValueError) -> list[str]:
    if isinstance(e, ValidationError):
        return [
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            if err.get("loc")
            else err["msg"]
            for err in e.errors()
        ]
    return [str(e)]
