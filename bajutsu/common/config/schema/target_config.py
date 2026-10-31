"""One app's block under `targets.<name>` — where every per-app difference is declared (BE-0452)."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from pydantic import Field, ValidationError, model_validator

from bajutsu.common.scenario import Interrupt, tip_kit_entry

from ._model import _Model
from .ai_settings import AiSettings
from .evidence import Evidence
from .hooks import Hooks, _nested_steps
from .paths import Paths
from .platform.registry import schema_for, validate_group
from .services import Services

# The nine keys a target accepts, in the order the documentation lists them.
TARGET_KEYS: tuple[str, ...] = (
    "platform",
    "subject",
    "runsOn",
    "driver",
    "services",
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

# Where each field of the removed `run` group went (BE-0473), for a target, `defaults`, and
# `defaults.platforms.<platform>` alike. Pydantic's generic extra-field error names no replacement.
MOVED_RUN_KEYS: dict[str, str] = {
    # `defaults` has no `subject`, so a shared erase goes under its platform block.
    "erase": "subject.reinstall: erase (in defaults, platforms.<platform>.subject.reinstall)",
    "network": "evidence.collectNetwork",
    "visualCompare": "evidence.visualCompare",
    "secrets": "evidence.secrets",
    "systemAlertHandling": "hooks.interrupts: one `- systemAlert: <prompt>` entry with its `choice` "
    "per rule, carrying pollInterval and frozenScreenTimeout on the entries they serve",
    "tipKitHandling": "hooks.interrupts: `- tipKit: dismiss`",
    "ignoredSettings": "runsOn.ignoredSettings",
}


def run_moves(where: str, run: Any) -> str:
    """The message naming where each key of a removed `run` block went (BE-0473)."""
    keys = [k for k in run if k in MOVED_RUN_KEYS] if isinstance(run, dict) else []
    moves = [f"{where}.{k} moved to {MOVED_RUN_KEYS[k]}" for k in keys]
    return "; ".join(moves) or f"{where} was removed"


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

    `platform` decides the actuator and selects the models that validate the three platform-shaped
    groups (`subject`, `runsOn`, and `driver`) from the registry, so the core names no platform here. Every model forbids extra keys, so a key another platform takes
    fails as unknown, with the accepted fields listed.
    """

    platform: str
    # Validated by the registered model for `platform`; the concrete type is that model.
    subject: _Model
    runs_on: _Model = Field(alias="runsOn")
    driver: _Model
    services: Services = Field(default_factory=Services)
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
            if not isinstance(entry, Interrupt):
                continue
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

    @model_validator(mode="after")
    def _tip_kit_needs_ios(self) -> TargetConfig:
        # TipKit is Apple's, so the entry has nothing to dismiss on another platform.
        if self.platform != "ios" and tip_kit_entry(self.hooks.interrupts) is not None:
            raise ValueError(
                f"hooks.interrupts: a `tipKit` entry needs an iOS target; platform "
                f"{self.platform!r} shows no TipKit popover"
            )
        return self

    @model_validator(mode="before")
    @classmethod
    def _platform_groups(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "run" in data:
            raise ValueError(run_moves("targets.<name>.run", data["run"]) + " (BE-0473)")
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
        return out
