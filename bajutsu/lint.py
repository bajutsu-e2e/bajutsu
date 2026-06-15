"""Scenario linter — validate YAML scenarios without running them.

Reuses the existing Pydantic schema validation, component expansion, and
data-driven expansion. Returns a list of human-readable error strings
(empty = valid).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from bajutsu.scenario import load_scenario_file


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


def scenario_json_schema() -> str:
    """Return the JSON Schema for a scenario file (a list of Scenario objects)."""
    import json

    from bajutsu.scenario import ScenarioFile

    return json.dumps(ScenarioFile.model_json_schema(), indent=2, ensure_ascii=False)


def _format_validation_error(e: ValidationError | ValueError) -> list[str]:
    if isinstance(e, ValidationError):
        return [
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            if err.get("loc")
            else err["msg"]
            for err in e.errors()
        ]
    return [str(e)]
