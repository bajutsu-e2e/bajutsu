"""Determinism-audit serve operation (BE-0145).

Surfaces the static determinism audit (`bajutsu/audit.py`, BE-0049) in the web UI. Like the CLI,
it is read-only, device-free, and AI-free: it grades each selector on the stability ladder and
flags over-loose waits and coordinate gestures, but never runs a device, calls a model, or decides
a verdict. It accepts either inline `yaml` (the editor's live, possibly-unsaved content) or a
`{target, path}` pair the server reads from disk (the Replay view) — the audit itself is the same
pure function of the parsed scenario either way.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import yaml

from bajutsu import audit as _audit
from bajutsu.scenario import load_scenario_file
from bajutsu.serve.jobs import ServeState
from bajutsu.serve.operations.reads import read_scenario


def audit_scenario(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Grade a scenario's static determinism for the editor / Replay panel.

    The scenario source is the inline ``yaml`` when present (the editor's live content), else the
    saved file addressed by ``{target, path}`` (the Replay view), read through the org-scoped store
    so cross-tenant paths read as not-found. Returns one report per scenario in the file; a report
    always carries a grade even with no selectors.

    Returns:
        JSON payload and HTTP status. ``{ok, reports}`` on success; ``{error}`` with 400 for a
        missing input or unparseable scenario, or 404 for an unknown ``{target, path}``.
    """
    inline = body.get("yaml")
    if inline is not None:
        text = str(inline)
    else:
        target, path = body.get("target"), body.get("path")
        if not target or not path:
            return {"error": "yaml, or target and path, is required"}, 400
        if state.config is None:
            return {"error": "open a config first"}, 400
        result, status = read_scenario(state, str(target), str(path), actor=actor)
        if status != 200:
            return result, status
        text = str(result["yaml"])

    try:
        scenarios = load_scenario_file(text).scenarios
    except (ValueError, yaml.YAMLError) as e:
        return {"error": f"could not parse scenario: {e}"}, 400

    reports = [dataclasses.asdict(_audit.audit_scenario(s)) for s in scenarios]
    return {"ok": True, "reports": reports}, 200
