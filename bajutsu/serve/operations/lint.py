"""Inline scenario validation for the serve editor (BE-0138).

Static and AI-free: `lint_scenario` runs the same `bajutsu/common/lint.py` the CLI does, and
`scenario_schema` serves the scenario JSON Schema. Neither touches a device or a model. With a
target selected, `lint_scenario` also runs the target checks `bajutsu lint --config --target` runs
(BE-0467), resolving that target against the session's bound config."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from bajutsu.common.config import Effective, load_config_file, resolve
from bajutsu.common.lint import Diagnostic, lint_diagnostics, scenario_json_schema
from bajutsu.serve.operations._common import _resolve_org_or_forbid
from bajutsu.serve.state import ServeState


def lint_scenario(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None, session: str | None = None
) -> tuple[Any, int]:
    """Validate the editor's YAML, returning line-anchored diagnostics (`ok` = no findings).

    Args:
        state: The serve state whose bound config resolves the selected target.
        body: ``{yaml, target?, path?}`` — the editor's buffer, its selected target, and the
            scenario's path in that target's scenarios dir, beside which its file refs resolve.
        actor: The requesting user, for the org the target must belong to.
        session: The requesting session, whose binding names the config.

    Returns:
        ``({ok, diagnostics}, 200)``, or ``({error}, 403)`` for a target in another org. With no
        target, or no bound config, only the schema is checked.
    """
    text = str(body.get("yaml", ""))
    diagnostics = lint_diagnostics(text)
    target = str(body.get("target") or "")
    if not diagnostics and target:
        org, forbidden = _resolve_org_or_forbid(state, target, actor, session)
        if forbidden:
            return forbidden
        diagnostics = _target_diagnostics(state, text, target, body.get("path"), org, session)
    return {"ok": not diagnostics, "diagnostics": diagnostics}, 200


def _target_diagnostics(
    state: ServeState,
    text: str,
    target: str,
    ref: object,
    org: str,
    session: str | None,
) -> list[Diagnostic]:
    """The target checks' findings on *text*, as diagnostics; none without a bound config."""
    from bajutsu.common.target_lint import TargetRequired, lint_against_targets

    binding = state.binding_for(session, org)
    if binding.config is None:
        return []
    try:
        config = load_config_file(binding.config)
    except (OSError, ValueError) as e:
        return [_diagnostic(None, f"could not check against target '{target}': {e}")]

    def effective(name: str) -> Effective:
        return resolve(config, name).rebased(binding.cwd, confine=False)

    scope = state.for_org(org).scenarios.scope(target, session=session, org=org)
    directory = scope.directory() if scope is not None else None
    with tempfile.TemporaryDirectory() as empty:
        # A store with no files on this host (server mode) still expands a scenario's own
        # `components:`; an empty root keeps any file ref from reaching a host path.
        root = directory or Path(empty)
        name = Path(ref).name if isinstance(ref, str) and ref else "scenario.yaml"
        try:
            findings = lint_against_targets(text, root / name, effective, target, root=root)
        except (TargetRequired, KeyError, OSError, ValueError) as e:
            return [_diagnostic(None, f"could not check against target '{target}': {e}")]
    return [_diagnostic(f.line, f"{f.scenario} [target {f.target}]: {f.message}") for f in findings]


def _diagnostic(line: int | None, message: str) -> Diagnostic:
    """One error finding, anchored to *line* when known."""
    return {"line": line, "column": None, "message": message, "severity": "error"}


def scenario_schema() -> tuple[Any, int]:
    """Serve the scenario JSON Schema the editor consumes for validation and completion."""
    return json.loads(scenario_json_schema()), 200
