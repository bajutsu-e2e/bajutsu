"""Scoped scenario edits for the serve Author editor (BE-0261).

Static and AI-free, like `lint_scenario`: these transform the YAML the editor holds — Edit's Apply
writes a picked selector into a step, Enrich's Accept inserts proposed assertions and a settle wait
— through the scenario model and the canonical serializer (`bajutsu/scenario/edit.py`), never by
string-splicing in the browser. They operate purely on the request payload, so they need no
`ServeState`. The result flows back into the textarea for the existing live lint before a human
Saves; nothing here bypasses `save_scenario`.
"""

from __future__ import annotations

from typing import Any

import yaml

from bajutsu.scenario.edit import EditError, apply_enrichment, apply_selector


def apply_selector_edit(body: dict[str, Any]) -> tuple[Any, int]:
    """Write the resolved selector into the named step, returning the round-tripped YAML."""
    yaml_text = str(body.get("yaml", ""))
    scenario = str(body.get("scenario", ""))
    selector = body.get("selector")
    step_index = body.get("stepIndex")
    if not isinstance(selector, dict) or not selector:
        return {"error": "selector must be a non-empty object"}, 400
    if not isinstance(step_index, int) or isinstance(step_index, bool):
        return {"error": "stepIndex must be an integer"}, 400
    try:
        result = apply_selector(yaml_text, scenario, step_index, selector)
    except EditError as e:
        return {"error": str(e)}, 400
    except (ValueError, yaml.YAMLError) as e:
        return {"error": f"invalid scenario: {e}"}, 400
    return {"ok": True, "yaml": result}, 200


def apply_enrichment_edit(body: dict[str, Any]) -> tuple[Any, int]:
    """Insert Enrich's proposed assertions and settle wait, returning the round-tripped YAML."""
    yaml_text = str(body.get("yaml", ""))
    scenario = str(body.get("scenario", ""))
    expect = body.get("expect")
    settle = body.get("settle")
    if not isinstance(expect, list):
        return {"error": "expect must be a list of assertions"}, 400
    if settle is not None and not isinstance(settle, dict):
        return {"error": "settle must be an object or null"}, 400
    if not any(isinstance(a, dict) for a in expect) and settle is None:
        return {"error": "nothing to apply"}, 400
    try:
        result = apply_enrichment(yaml_text, scenario, expect, settle)
    except EditError as e:
        return {"error": str(e)}, 400
    except (ValueError, yaml.YAMLError) as e:
        return {"error": f"invalid scenario: {e}"}, 400
    return {"ok": True, "yaml": result}, 200
