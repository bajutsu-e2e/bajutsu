"""Scenario linter — validate YAML scenarios without running them.

Reuses the existing Pydantic schema validation (``load_scenario_file``).
Returns a list of human-readable error strings (empty = valid).
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import yaml
from pydantic import ValidationError

from bajutsu import _yaml
from bajutsu.scenario import Scenario, load_scenario_file


class Diagnostic(TypedDict):
    """One line-anchored lint finding, for inline display in the serve editor (BE-0138)."""

    line: int | None  # 1-based source line, when it can be resolved; None otherwise.
    column: int | None  # 1-based column, known only for YAML parse errors.
    message: str  # Human-readable description of the problem.
    severity: str  # "error" — the only level lint emits today.


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


def lint_diagnostics(text: str) -> list[Diagnostic]:
    """Validate scenario YAML into line-anchored diagnostics (empty = valid).

    The line-anchored counterpart of `lint_text`: the serve editor renders these inline. YAML
    parse errors carry the exact mark; validation errors resolve their location best-effort by
    walking the YAML node tree, falling back to `line: None` when the path cannot be followed.
    """
    try:
        load_scenario_file(text)
    except ValidationError as e:
        return _diagnostics_from_validation(text, e)
    except yaml.YAMLError as e:
        return [_diagnostic_from_yaml_error(e)]
    except ValueError as e:
        return [Diagnostic(line=None, column=None, message=str(e), severity="error")]
    return []


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


def _diagnostic_from_yaml_error(e: yaml.YAMLError) -> Diagnostic:
    """A parse error carries an exact source mark on the `MarkedYAMLError` subclasses PyYAML raises."""
    mark = getattr(e, "problem_mark", None)
    line = mark.line + 1 if mark is not None else None
    column = mark.column + 1 if mark is not None else None
    problem = getattr(e, "problem", None)
    return Diagnostic(
        line=line,
        column=column,
        message=f"YAML parse error: {problem or e}",
        severity="error",
    )


def _diagnostics_from_validation(text: str, e: ValidationError) -> list[Diagnostic]:
    """Turn each pydantic error into a diagnostic, resolving its `loc` to a source line.

    The node tree is composed once from the same text so every error's location is walked against
    the real document; a compose failure (unreachable here — the YAML already parsed) degrades to
    unanchored diagnostics rather than raising."""
    try:
        root = yaml.compose(text, Loader=_yaml._Loader)
    except yaml.YAMLError:
        root = None
    return [
        Diagnostic(
            line=_resolve_line(root, err["loc"]),
            column=None,
            message=f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            if err.get("loc")
            else err["msg"],
            severity="error",
        )
        for err in e.errors()
    ]


def _resolve_line(root: yaml.Node | None, loc: tuple[int | str, ...]) -> int | None:
    """Best-effort 1-based line for a pydantic `loc` path within the YAML node tree.

    Walks each `loc` segment (str = mapping key, int = sequence index) into the tree, tracking the
    deepest node reached; a bare-list file has its synthetic leading `scenarios` key stripped, since
    `load_scenario_file` injects it. Stops at the first segment that cannot be followed (e.g. a
    missing key) and anchors to the last node it did reach — better a nearby line than none."""
    if root is None:
        return None
    node: yaml.Node = root
    if isinstance(root, yaml.SequenceNode) and loc and loc[0] == "scenarios":
        loc = loc[1:]
    line = root.start_mark.line
    for seg in loc:
        child = _descend(node, seg)
        if child is None:
            break
        node = child
        line = node.start_mark.line
    return line + 1


def _descend(node: yaml.Node, seg: int | str) -> yaml.Node | None:
    """The child node for one `loc` segment: an index into a sequence, or a key of a mapping."""
    if isinstance(node, yaml.SequenceNode) and isinstance(seg, int):
        return node.value[seg] if -len(node.value) <= seg < len(node.value) else None
    if isinstance(node, yaml.MappingNode) and isinstance(seg, str):
        return next((v for k, v in node.value if k.value == seg), None)
    return None
