"""The config's JSON Schema, generated from the models and the platform registry (BE-0452).

`bajutsu config schema` prints it for editor integration. A target's four platform-shaped groups
are validated by the models its `platform` selects, so the schema states each platform's target as
its own branch of a `oneOf` with `platform` as the discriminator: an editor that reads the schema
completes the keys of the platform written. Nothing here is written by hand; a new platform in the
registry appears in the schema with no edit.
"""

from __future__ import annotations

import copy
from typing import Annotated, Any, Literal, Union

from pydantic import Field, create_model

from .schema._model import _Model
from .schema.config import Config
from .schema.platform.registry import GROUPS, PLATFORM_SCHEMAS
from .schema.target_config import TargetConfig, run_model

# The JSON Schema dialect Pydantic emits.
DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _target_model(platform: str) -> type[_Model]:
    """One platform's target as a model of its own: the shared keys plus its registered groups.

    A target's groups are optional in the file, because `defaults.platforms.<platform>` may supply
    them before the target is validated.
    """
    schema = PLATFORM_SCHEMAS[platform]
    groups: dict[str, type[_Model]] = {
        "app": schema.app,
        "runs_on": schema.runs_on,
        "driver": schema.driver,
        "run": run_model(platform),
    }
    fields: dict[str, Any] = {"platform": (Literal[platform], ...)}
    for name, info in TargetConfig.model_fields.items():
        if name == "platform":
            continue
        if name in groups:
            fields[name] = (groups[name] | None, Field(default=None, alias=info.alias))
        else:
            fields[name] = (info.annotation, info)
    title = f"{platform.capitalize()}Target"
    return create_model(
        title, __base__=_Model, __doc__=f"A target of platform `{platform}`.", **fields
    )


def _group_defaults(defs: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    """A group model's schema as `defaults.platforms` accepts it: every field optional.

    Defaults hold part of a group, which each target completes, so no field is required here.
    """
    name = ref["$ref"].rsplit("/", 1)[-1]
    partial: dict[str, Any] = copy.deepcopy(defs[name])
    partial.pop("required", None)
    return partial


def config_json_schema() -> dict[str, Any]:
    """The JSON Schema of `bajutsu.config.yaml`, generated from the models and the registry.

    Returns:
        The schema as a plain dict: each target a `oneOf` over the registered platforms, keyed by
        `platform`, and `defaults.platforms` keyed by the same platforms with every field optional.
    """
    branches = tuple(_target_model(p) for p in PLATFORM_SCHEMAS)
    target = Annotated[Union[branches], Field(discriminator="platform")]  # type: ignore[valid-type]  # noqa: UP007 - a runtime tuple of models has no `|` spelling
    fields: dict[str, Any] = {
        name: (info.annotation, info) for name, info in Config.model_fields.items()
    }
    fields["targets"] = (dict[str, target], Field(default_factory=dict))
    model = create_model("Config", __base__=_Model, __doc__=Config.__doc__, **fields)
    schema = model.model_json_schema(by_alias=True, mode="validation")
    defs: dict[str, Any] = schema["$defs"]
    platforms: dict[str, Any] = {}
    for platform, branch in zip(PLATFORM_SCHEMAS, branches, strict=True):
        props = defs[branch.__name__]["properties"]
        group_props: dict[str, Any] = {}
        for group in GROUPS:
            ref = next(s for s in props[group]["anyOf"] if "$ref" in s)
            group_props[group] = _group_defaults(defs, ref)
        platforms[platform] = {
            "type": "object",
            "properties": group_props,
            "additionalProperties": False,
        }
    defs["Defaults"]["properties"]["platforms"] = {
        "type": "object",
        "description": "The platform-shaped groups for each platform's targets, merged into them.",
        "properties": platforms,
        "additionalProperties": False,
    }
    defs.pop("PlatformDefaults", None)
    # The loader drops a top-level `x-` key, which hosts a YAML anchor (BE-0473); an editor
    # validating against this schema would otherwise flag the documented pattern.
    schema["patternProperties"] = {"^x-": {}}
    return {"$schema": DIALECT, **schema}


def scenario_runs_on_schema() -> dict[str, Any]:
    """The JSON Schema of a scenario's `preconditions.runsOn`, keyed by platform from the registry.

    The scenario model keeps each block as written, since the scenario schema never imports the
    config; this states what `runs_on_problems` accepts instead: each platform's `runsOn` fields
    except how a target is driven (`kind`, `emulate`), every one optional.

    Returns:
        The schema, with the definitions its fields refer to under `$defs`.
    """
    from .preconditions import _TARGET_ONLY_RUNS_ON

    platforms: dict[str, Any] = {}
    defs: dict[str, Any] = {}
    for platform, schema in PLATFORM_SCHEMAS.items():
        generated = schema.runs_on.model_json_schema(by_alias=True, mode="validation")
        defs.update(generated.pop("$defs", {}))
        properties = {
            k: v
            for k, v in generated.get("properties", {}).items()
            if k not in _TARGET_ONLY_RUNS_ON
        }
        platforms[platform] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": platforms,
        "additionalProperties": False,
        "$defs": defs,
    }
