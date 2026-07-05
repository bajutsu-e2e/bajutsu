"""Inline scenario validation for the serve editor (BE-0138).

Static and AI-free: `lint_scenario` runs the same `bajutsu/lint.py` the CLI does, and
`scenario_schema` serves the scenario JSON Schema. Neither touches a device, a model, or the
filesystem — they validate the YAML the editor sends, so they need no `ServeState`."""

from __future__ import annotations

import json
from typing import Any

from bajutsu.lint import lint_diagnostics, scenario_json_schema


def lint_scenario(body: dict[str, Any]) -> tuple[Any, int]:
    """Validate the editor's YAML, returning line-anchored diagnostics (`ok` = no findings)."""
    diagnostics = lint_diagnostics(str(body.get("yaml", "")))
    return {"ok": not diagnostics, "diagnostics": diagnostics}, 200


def scenario_schema() -> tuple[Any, int]:
    """Serve the scenario JSON Schema the editor consumes for validation and completion."""
    return json.loads(scenario_json_schema()), 200
