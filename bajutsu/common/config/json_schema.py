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

    A target's groups are optional in the file, because `defaults.platforms.<platform>` or a
    template may supply them before the target is validated.
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
    fields["extends"] = (
        str | list[str] | None,
        Field(
            default=None,
            description="Templates folded under this block, a name or a file path; later wins.",
        ),
    )
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
    schema["properties"]["templates"] = _templates_schema(defs, branches, platforms)
    return {"$schema": DIALECT, **schema}


def _templates_schema(
    defs: dict[str, Any], branches: tuple[type[_Model], ...], platforms: dict[str, Any]
) -> dict[str, Any]:
    """The `templates` map: each entry a partial target, of one platform or of none (BE-0471).

    An `anyOf`, not the targets' discriminated `oneOf`, because a template may omit `platform` and a
    discriminator has no branch for that. A template that names its platform is that platform's
    target with every field optional; one that does not forbids `platform` and takes any platform's
    partial shape for the platform-shaped groups.
    """
    refs: list[dict[str, str]] = []
    for platform, branch in zip(PLATFORM_SCHEMAS, branches, strict=True):
        template = copy.deepcopy(defs[branch.__name__])
        template["title"] = f"{platform.capitalize()}Template"
        template["description"] = f"A template of platform `{platform}`, every field optional."
        template["required"] = ["platform"]
        # Nullable like the target branches' groups: a template's `null` clears what `defaults` set.
        template["properties"].update(
            {
                group: {"anyOf": [partial, {"type": "null"}]}
                for group, partial in platforms[platform]["properties"].items()
            }
        )
        defs[template["title"]] = template
        refs.append({"$ref": f"#/$defs/{template['title']}"})
    any_platform = copy.deepcopy(defs[branches[0].__name__])
    del any_platform["properties"]["platform"]
    any_platform.pop("required", None)
    for group in GROUPS:
        any_platform["properties"][group] = {
            "anyOf": [
                *(platforms[p]["properties"][group] for p in PLATFORM_SCHEMAS),
                {"type": "null"},
            ]
        }
    any_platform["title"] = "PlatformlessTemplate"
    any_platform["description"] = "A template with no `platform`, which its targets supply."
    defs["PlatformlessTemplate"] = any_platform
    refs.append({"$ref": "#/$defs/PlatformlessTemplate"})
    return {
        "type": "object",
        "description": "Named, partial target blocks a target pulls in with `extends`.",
        "additionalProperties": {"anyOf": refs},
    }
