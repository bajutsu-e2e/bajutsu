"""The team-wide `defaults:` block merged into each target (BE-0452)."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, model_validator

from ._model import _Model
from .ai_settings import AiSettings
from .evidence import Evidence
from .hooks import Hooks
from .paths import Paths
from .platform._shared import IGNORED_SETTINGS, destination_problems
from .platform.registry import GROUPS, allowed_fields, schema_for
from .services import Services
from .target_config import run_moves

DEFAULTS_KEYS: tuple[str, ...] = (
    "services",
    "hooks",
    "evidence",
    "paths",
    "ai",
    "platforms",
)


class PlatformDefaults(_Model):
    """`defaults.platforms.<platform>`: the platform-shaped groups for that platform's targets.

    Kept as written: each target validates the merged result with its platform's models, which is
    where a required field such as `subject.id` may come from here. Only the top-level keys of each group
    are checked now, so a misspelled one fails even when no target of the platform exists.
    """

    subject: dict[str, Any] = Field(default_factory=dict)
    runs_on: dict[str, Any] = Field(default_factory=dict, alias="runsOn")
    driver: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _moved_app(cls, data: Any) -> Any:
        # Named here rather than left to the generic extra-field error, as a target's `app` is.
        if isinstance(data, dict) and "app" in data:
            raise ValueError(
                "`app` moved to `subject`; the fields inside keep their names (BE-0470)"
            )
        if isinstance(data, dict) and "run" in data:
            raise ValueError(run_moves("run", data["run"]) + " (BE-0473)")
        return data


class Defaults(_Model):
    """Team-wide defaults under `defaults:`, merged into each target before it is validated.

    The shared groups sit directly here; the platform-shaped groups sit under `platforms.<platform>`.
    `ai` merges field by field in `resolve`; every other dictionary merges key by key, with the
    target winning, and the lists replace except `evidence.secrets` and `evidence.redact`, which union.
    """

    services: Services | None = None
    hooks: Hooks | None = None
    evidence: Evidence | None = None
    paths: Paths | None = None
    # Team-wide AI provider/model/endpoint/key (BE-0047), overridable per target. None = env-only.
    ai: AiSettings | None = None
    platforms: dict[str, PlatformDefaults] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _known_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and "run" in data:
            raise ValueError(run_moves("defaults.run", data["run"]) + " (BE-0473)")
        if isinstance(data, dict) and (unknown := [k for k in data if k not in DEFAULTS_KEYS]):
            raise ValueError(
                f"unknown defaults field(s) {', '.join(map(repr, unknown))}; defaults accepts "
                f"{', '.join(DEFAULTS_KEYS)} (BE-0452 regrouped the flat keys)"
            )
        return data

    @model_validator(mode="after")
    def _no_ignored_settings(self) -> Self:
        # An ignored setting belongs to one environment, so it is declared on that target alone
        # (BE-0467); a team-wide default would hide a gap on every route at once.
        if any(IGNORED_SETTINGS in g.runs_on for g in self.platforms.values()):
            raise ValueError(
                "defaults may not set runsOn.ignoredSettings; declare it on the target whose "
                "environment cannot apply the setting (targets.<name>.runsOn.ignoredSettings)"
            )
        return self

    @model_validator(mode="after")
    def _known_platform_fields(self) -> Self:
        for platform, groups in self.platforms.items():
            schema = schema_for(platform)
            for group in GROUPS:
                written = {
                    "subject": groups.subject,
                    "runsOn": groups.runs_on,
                    "driver": groups.driver,
                }[group]
                if group == "runsOn":
                    # A flat key is named with the destination path it moved to (BE-0469).
                    if problems := destination_problems(schema.model(group), written):
                        raise ValueError(f"defaults.platforms.{platform}: {'; '.join(problems)}")
                    continue
                accepted = allowed_fields(schema.model(group))
                if unknown := [k for k in written if k not in accepted]:
                    raise ValueError(
                        f"defaults.platforms.{platform}.{group}: unknown field(s) "
                        f"{', '.join(map(repr, unknown))}; allowed fields: "
                        f"{', '.join(accepted) or '(none)'}"
                    )
        return self
