"""One app's block under `targets.<name>` — where every per-app difference is declared (BE-0452)."""

from __future__ import annotations

from collections.abc import Callable
from functools import cache, partial
from typing import Any, cast

from pydantic import Field, ValidationError, ValidationInfo, create_model, model_validator

from ._model import _Model
from .ai_settings import AiSettings
from .evidence import Evidence
from .hooks import Hooks, _nested_steps
from .paths import Paths
from .platform.registry import schema_for, validate_group, validate_model
from .run_policy import RunPolicy
from .services import Services

# The ten keys a target accepts, in the order the documentation lists them.
TARGET_KEYS: tuple[str, ...] = (
    "platform",
    "subject",
    "runsOn",
    "driver",
    "services",
    "run",
    "hooks",
    "evidence",
    "paths",
    "ai",
)

# A key a target no longer accepts, and where its value goes now. The loader reads none of them, so
# the error is the whole migration path an author gets.
MOVED_TARGET_KEYS: dict[str, str] = {
    "app": "`subject`, the thing under test; the fields inside keep their names (BE-0470)",
    "notify": "the top-level `notify` list, which alone says where results go; an endpoint's "
    "`targets:` narrows it to scenario names (BE-0473)",
}


@cache
def run_model(platform: str) -> type[RunPolicy]:
    """The model of a `platform` target's `run`: the shared run policy joined to the platform's fields.

    Raises:
        ValueError: No platform of that name is registered.
    """
    extension = schema_for(platform).run
    joined = create_model(f"{extension.__name__}Policy", __base__=(extension, RunPolicy))
    return cast("type[RunPolicy]", joined)


def _validated(where: str, validate: Callable[[], _Model]) -> _Model:
    # A group's own failure (a missing `subject.id`, say) reads as `subject.id: Field required` rather than
    # as a nested Pydantic report, so the target's location prefix stays the only one shown.
    try:
        return validate()
    except ValidationError as exc:
        raise ValueError(
            "\n".join(
                f"{'.'.join(str(p) for p in (where, *e['loc']))}: {e['msg']}" for e in exc.errors()
            )
        ) from None


class TargetConfig(_Model):
    """One app's config under `targets.<name>`, after `defaults` has been merged into it.

    `platform` decides the actuator and selects the models that validate the four platform-shaped
    groups (`subject`, `runsOn`, `driver`, and the platform's fields of `run`) from the registry, so the
    core names no platform here. Every model forbids extra keys, so a key another platform takes
    fails as unknown, with the accepted fields listed.
    """

    platform: str
    # Validated by the registered model for `platform`; the concrete type is that model.
    subject: _Model
    runs_on: _Model = Field(alias="runsOn")
    driver: _Model
    services: Services = Field(default_factory=Services)
    # The shared run policy joined to the platform's own fields (`run_model`).
    run: RunPolicy = Field(default_factory=RunPolicy)
    hooks: Hooks = Field(default_factory=Hooks)
    evidence: Evidence = Field(default_factory=Evidence)
    paths: Paths = Field(default_factory=Paths)
    # Per-target AI provider/model/endpoint/key (BE-0047), overriding `defaults.ai` field by field.
    ai: AiSettings | None = None

    @model_validator(mode="after")
    def _attribute_needs_web(self) -> TargetConfig:
        # The capability preflight reads the scenario alone, so a config-declared interrupt is checked
        # here instead (BE-0466). On the merged target, not on `Hooks`, which carries no platform —
        # and `defaults.hooks` has already merged in, so both shapes are judged under this platform.
        if schema_for(self.platform).element_attribute:
            return self
        for i, entry in enumerate(self.hooks.interrupts):
            conditions = [entry.condition]
            for step in _nested_steps(entry.steps):
                conditions.extend(step.assert_ or [])
                if step.if_ is not None:
                    conditions.append(step.if_.condition)
            if any(a.attribute is not None for a in conditions):
                raise ValueError(
                    f"hooks.interrupts[{i}]: an `attribute` assertion needs a web target; "
                    f"platform {self.platform!r} cannot read an HTML attribute (BE-0466)"
                )
        return self

    @model_validator(mode="before")
    @classmethod
    def _platform_groups(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict):
            return data
        for old, new in MOVED_TARGET_KEYS.items():
            if old in data:
                raise ValueError(f"targets.<name>.{old} moved to {new}")
        unknown = [k for k in data if k not in TARGET_KEYS]
        if unknown:
            raise ValueError(
                f"unknown target field(s) {', '.join(map(repr, unknown))}; a target accepts "
                f"{', '.join(TARGET_KEYS)} (BE-0452 regrouped the flat keys)"
            )
        platform = data.get("platform")
        if not isinstance(platform, str):
            raise ValueError(  # noqa: TRY004 - Pydantic turns only ValueError into a field error
                "a target needs `platform`: one of ios, android, web, or fake"
            )
        schema_for(platform)  # an unknown platform fails here, listing the registered ones
        out = dict(data)
        for group in ("subject", "runsOn", "driver"):
            out[group] = _validated(
                group, partial(validate_group, platform, group, data.get(group), where=group)
            )
        out["run"] = _validated(
            "run",
            partial(
                validate_model,
                run_model(platform),
                data.get("run"),
                platform=platform,
                where="run",
                context=info.context,
            ),
        )
        return out
