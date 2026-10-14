"""Parse scenario / component YAML into the validated models."""

from __future__ import annotations

from typing import Any

from bajutsu.common import _yaml
from bajutsu.common.scenario.models import Component, Scenario, ScenarioFile
from bajutsu.common.scenario.models.scenario import SCHEMA_VERSION


def _as_schema_int(value: object) -> int | None:
    """Interpret a declared schema the way the non-strict `schema` field's int coercion would.

    Mirrors pydantic's lax int coercion (int, integral float, numeric string) so the version gate
    sees the same value validation will — otherwise `schema: "2"` or `schema: 2.0` would coerce to a
    valid int *after* the gate and slip an out-of-range version through. A value pydantic can't coerce
    (or a bool, which is not a version) is left for validation to reject with a type error.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _check_schema_version(data: dict[str, object]) -> None:
    """Reject an out-of-range schema before full validation, so the mismatch is what's reported (BE-0119)."""
    # `schema` is the on-disk key; `schema_version` is its populate_by_name spelling. An absent key
    # means the implicit version 1.
    declared = _as_schema_int(data.get("schema", data.get("schema_version")))
    if declared is None:
        return
    if declared > SCHEMA_VERSION:
        raise ValueError(
            f"scenario file uses schema {declared}, but this bajutsu understands up to "
            f"schema {SCHEMA_VERSION} — upgrade bajutsu or pin an older scenario/config version"
        )
    if declared < 1:
        raise ValueError(f"scenario file declares schema {declared}; the earliest schema is 1")


def load_scenario_file(text: str) -> ScenarioFile:
    """Parse a scenario file: a list of scenarios, or a `{description, scenarios}` mapping.

    Raises:
        ValueError: The top level is neither a list nor a mapping (§6.1), or the file declares a
            schema version newer than this bajutsu supports (BE-0119).
    """
    data = _yaml.safe_load(text)
    if isinstance(data, list):
        return ScenarioFile.model_validate({"scenarios": data})
    if isinstance(data, dict):
        _check_schema_version(data)
        return ScenarioFile.model_validate(data)
    raise ValueError(
        "scenario file must be a list of scenarios or a {description, scenarios} mapping (§6.1)"
    )


def load_scenarios(text: str) -> list[Scenario]:
    """Parse a scenario file into validated Scenario objects (any file-level description dropped)."""
    return load_scenario_file(text).scenarios


def load_component(text: str) -> Component:
    """Parse a YAML string (a single component mapping) into a validated Component."""
    return Component.model_validate(_yaml.safe_load(text))


def renamed_outcome(value: Any) -> Any:
    """*value* with the failed-outcome word BE-0452 renamed (`error`) read as `failure`."""
    return "failure" if value == "error" else value


# The lifecycle phase keys BE-0452 renamed, read from a scenario plan stored before it.
_PHASE_RENAMES = {"before": "setup", "after": "cleanup"}


def _upgrade_scenario(raw: Any) -> None:
    """Rewrite one stored scenario's pre-BE-0452 spellings in place, so an earlier run still loads.

    The scenario model rejects the old keys (BE-0452 reads none of them), but a run directory keeps
    the plan it ran, so only this offline reader translates them: `before` / `after` become `setup`
    / `cleanup`, and the outcome word `error` in a cleanup rule's `on` and a `capturePolicy`
    trigger's `result` becomes `failure`.
    """
    if not isinstance(raw, dict):
        return
    for old, new in _PHASE_RENAMES.items():
        if old in raw and new not in raw:
            raw[new] = raw.pop(old)
    for rule in raw.get("cleanup") or []:
        if isinstance(rule, dict):
            rule["on"] = renamed_outcome(rule.get("on"))
    for rule in raw.get("capturePolicy") or []:
        trigger = rule.get("on") if isinstance(rule, dict) else None
        if isinstance(trigger, dict) and "result" in trigger:
            trigger["result"] = renamed_outcome(trigger["result"])


def _upgraded_scenario_text(text: str) -> str:
    """*text* re-dumped with every scenario's pre-BE-0452 spellings rewritten."""
    data = _yaml.safe_load(text)
    scenarios = data.get("scenarios") if isinstance(data, dict) else data
    if not isinstance(scenarios, list):
        return text  # not a shape this reader upgrades; `load_scenario_file` reports it
    for raw in scenarios:
        _upgrade_scenario(raw)
    return _yaml.safe_dump(data)


def load_stored_scenario_file(text: str) -> ScenarioFile:
    """Parse the `scenario.yaml` a finished run stored, reading pre-BE-0452 spellings as today's.

    The offline readers of a run directory (the report re-render, `trace`, `triage`) use this rather
    than `load_scenario_file`, which rejects the old names an author must rewrite.

    Raises:
        ValueError: As `load_scenario_file`.
    """
    return load_scenario_file(_upgraded_scenario_text(text))
