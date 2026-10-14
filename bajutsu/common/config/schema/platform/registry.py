"""The platform schema registry: each platform's models for the groups whose shape it decides (BE-0452).

A target's `app`, `runsOn`, `driver`, and the extra fields of `run` differ by platform. The core
looks the platform up here instead of naming platforms in its own schema, so adding one (Flutter, for
example) means registering one more entry. The models are plain Pydantic, so loading a config never
imports Playwright or simctl.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from .._model import _Model
from .android import AndroidApp, AndroidDriver, AndroidRun, AndroidRunsOn
from .fake import FakeApp, FakeDriver, FakeRun, FakeRunsOn
from .ios import IosApp, IosDriver, IosRun, IosRunsOn
from .web import WebApp, WebDriver, WebRun, WebRunsOn

Group = Literal["app", "runsOn", "driver", "run"]
GROUPS: tuple[Group, ...] = ("app", "runsOn", "driver", "run")


@dataclass(frozen=True)
class PlatformSchema:
    """The models one platform registers, one per platform-shaped group.

    `run` holds only the platform's additions to the shared run policy, such as iOS's
    `tipKitHandling`; the shared fields are validated by the target, not here.
    """

    app: type[_Model]
    runs_on: type[_Model]
    driver: type[_Model]
    run: type[_Model]

    def model(self, group: Group) -> type[_Model]:
        """The model registered for `group`."""
        return {"app": self.app, "runsOn": self.runs_on, "driver": self.driver, "run": self.run}[
            group
        ]


# Keyed by the platform token; a test pins these keys to `backends.PLATFORMS`, so a platform cannot
# gain an actuator without registering its schema, nor a schema without an actuator.
PLATFORM_SCHEMAS: Mapping[str, PlatformSchema] = types.MappingProxyType(
    {
        "ios": PlatformSchema(app=IosApp, runs_on=IosRunsOn, driver=IosDriver, run=IosRun),
        "android": PlatformSchema(
            app=AndroidApp, runs_on=AndroidRunsOn, driver=AndroidDriver, run=AndroidRun
        ),
        "web": PlatformSchema(app=WebApp, runs_on=WebRunsOn, driver=WebDriver, run=WebRun),
        "fake": PlatformSchema(app=FakeApp, runs_on=FakeRunsOn, driver=FakeDriver, run=FakeRun),
    }
)


def schema_for(platform: str) -> PlatformSchema:
    """The registered schema for `platform`.

    Raises:
        ValueError: No platform of that name is registered; the message lists those that are.
    """
    try:
        return PLATFORM_SCHEMAS[platform]
    except KeyError:
        raise ValueError(
            f"invalid platform {platform!r}: use one of {', '.join(PLATFORM_SCHEMAS)}"
        ) from None


def allowed_fields(model: type[BaseModel]) -> tuple[str, ...]:
    """The keys `model` accepts, spelled as a config writes them (the alias where one exists)."""
    return tuple(f.alias or name for name, f in model.model_fields.items())


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    # A field's model, looking through `X | None` and list items, so a nested unknown key is
    # reported against the block it sits in rather than against the group.
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        found = _nested_model(arg)
        if found is not None:
            return found
    return None


def _model_at(model: type[BaseModel], loc: tuple[int | str, ...]) -> type[BaseModel]:
    current = model
    for part in loc:
        if isinstance(part, int):
            continue
        field = next(
            (f for name, f in current.model_fields.items() if part in (name, f.alias)), None
        )
        nested = _nested_model(field.annotation) if field is not None else None
        if nested is None:
            break
        current = nested
    return current


def validate_group(platform: str, group: Group, data: Any, *, where: str) -> _Model:
    """Validate one platform-shaped group of a target with the model its platform registers.

    Pydantic's own error for an unknown key names the key but not the alternatives, which leaves a
    reader of `runsOn: { browser: … }` on an iOS target guessing; this rewrites it to list every
    field the block accepts.

    Args:
        platform: The target's `platform` token.
        group: Which group `data` is.
        data: The group's merged value, as read from YAML.
        where: How the error names the group's location, such as `targets.site.runsOn`.

    Raises:
        ValueError: The platform is not registered, or a key is unknown to the platform's model; the
            message lists the fields the block accepts, and any other failure beside it.
        ValidationError: Any other validation failure, unchanged.
    """
    return validate_model(schema_for(platform).model(group), data, platform=platform, where=where)


def validate_model(
    model: type[_Model], data: Any, *, platform: str, where: str, context: Any = None
) -> _Model:
    """Validate *data* with *model*, rewriting an unknown-key error to list the accepted fields.

    `validate_group` for a model the caller composes itself, such as a target's `run`, which joins
    the shared run policy to the platform's own fields.

    Args:
        model: The model to validate with.
        data: The block's value, as read from YAML; None reads as an empty block.
        platform: The target's platform, named in the error.
        where: How the error names the block's location.
        context: The Pydantic validation context passed through to nested validators.

    Raises:
        ValueError: A key is unknown to the model; the message lists the fields the block accepts,
            and any other failure beside it.
        ValidationError: Any other validation failure, unchanged.
    """
    try:
        return model.model_validate(data if data is not None else {}, context=context)
    except ValidationError as exc:
        unknown = [e for e in exc.errors() if e["type"] == "extra_forbidden"]
        if not unknown:
            raise
        # The other failures ride along, so fixing the unknown key never uncovers a second error.
        lines = [
            f"{'.'.join(str(p) for p in (where, *e['loc']))}: {e['msg']}"
            for e in exc.errors()
            if e["type"] != "extra_forbidden"
        ]
        for err in unknown:
            loc = tuple(err["loc"])
            block = _model_at(model, loc[:-1])
            path = ".".join(str(p) for p in (where, *loc))
            lines.append(
                f"{path}: unknown field for platform {platform!r}; "
                f"allowed fields: {', '.join(allowed_fields(block)) or '(none)'}"
            )
        raise ValueError("\n".join(lines)) from None
