"""The team-wide `defaults:` block each target overlays."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from bajutsu.common.scenario import Redact

from ._functions import _as_list, _check_platform, _check_requires
from ._model import _Model
from .ai_settings import AiSettings
from .doctor_config import DoctorConfig


class Defaults(_Model):
    """Team-wide defaults under `defaults:`, overlaid by each target (see `resolve`)."""

    backend: list[str] = Field(default_factory=lambda: ["ios"])
    # Team-wide default platform (ios / android / web), overridable per target. None derives each
    # target's platform from its backend (BE-0009 Slice 4), so an existing config is unchanged.
    platform: str | None = None
    device: str = "iPhone 15"
    locale: str = "en_US"
    capture: list[str] = Field(
        default_factory=lambda: ["screenshot.after", "elements", "actionLog"]
    )
    redact: Redact = Field(default_factory=Redact)
    secrets: list[str] = Field(default_factory=list)
    # Team-wide AI provider/model/endpoint/key (BE-0047), overridable per target. None = env-only.
    ai: AiSettings | None = None
    reserved_namespaces: list[str] = Field(default_factory=list, alias="reservedNamespaces")
    # Configurable doctor thresholds (BE-0024). Always present; defaults to DoctorConfig() (0.9/0.7).
    doctor: DoctorConfig = Field(default_factory=DoctorConfig)
    visual_compare: Literal["exact", "pixelmatch"] | None = Field(
        default=None, alias="visualCompare"
    )
    # Team-wide capability tokens every target requires of the worker that runs it (BE-0166), e.g.
    # `[ios18, ipad]`. On the hosted backend these route the job to a worker advertising them; a
    # per-target `requires` adds to (never replaces) this. Empty = only the platform axis routes.
    # Deprecated (BE-0450): still honored, with a notice, until the runtime and device-class
    # requirement is derived from the target's `device`.
    requires: list[str] = Field(default_factory=list)

    @field_validator("backend", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return _as_list(v)

    @field_validator("platform")
    @classmethod
    def _valid_platform(cls, v: str | None) -> str | None:
        return _check_platform(v)

    @field_validator("requires")
    @classmethod
    def _deprecated_requires(cls, v: list[str]) -> list[str]:
        return _check_requires(v, "config field 'defaults.requires'")
