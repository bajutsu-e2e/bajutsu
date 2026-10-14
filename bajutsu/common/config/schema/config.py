"""The top of a parsed `bajutsu.config.yaml`: team defaults and per-target config."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from ._merge import merge_defaults
from ._model import _Model
from .defaults import Defaults
from .doctor_config import DoctorConfig
from .notify_endpoint import NotifyEndpoint
from .target_config import TargetConfig


class Config(_Model):
    """A parsed `bajutsu.config.yaml`: team `defaults`, per-target config, and team-wide settings.

    `defaults` is merged into each target before the target is validated, so a field a target's
    platform requires may come from `defaults.platforms.<platform>`. `reservedNamespaces` and
    `doctor` are team-wide rather than per target, so they sit at the top level.

    The hosted multi-tenancy `orgs:` block is a `serve` concern the core does not model (BE-0129);
    `parse_config_dict` drops it before validation so a run reading an org-bearing config keeps
    working, and `bajutsu.serve.orgs` owns the org model.
    """

    defaults: Defaults = Field(default_factory=Defaults)
    targets: dict[str, TargetConfig] = Field(default_factory=dict)
    notify: list[NotifyEndpoint] = Field(default_factory=list)
    reserved_namespaces: list[str] = Field(default_factory=list, alias="reservedNamespaces")
    # Configurable doctor thresholds (BE-0024). Always present; defaults to DoctorConfig() (0.9/0.7).
    doctor: DoctorConfig = Field(default_factory=DoctorConfig)

    @model_validator(mode="before")
    @classmethod
    def _merge_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        defaults = data.get("defaults")
        targets = data.get("targets")
        if not isinstance(defaults, dict) or not isinstance(targets, dict):
            return data
        merged = {
            name: merge_defaults(defaults, raw) if isinstance(raw, dict) else raw
            for name, raw in targets.items()
        }
        return {**data, "targets": merged}
