"""The top of a parsed `bajutsu.config.yaml`: team defaults and per-target config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, ValidationInfo, model_validator

from ._merge import merge_defaults
from ._model import _Model
from ._templates import check_templates, expand_extends
from .defaults import Defaults
from .doctor_config import DoctorConfig
from .notify_endpoint import NotifyEndpoint
from .target_config import TargetConfig


class Config(_Model):
    """A parsed `bajutsu.config.yaml`: team `defaults`, per-target config, and team-wide settings.

    `defaults` is merged into each target before the target is validated, so a field a target's
    platform requires may come from `defaults.platforms.<platform>`. `templates` are named, partial
    target blocks a target pulls in with `extends`, folded between `defaults` and the target itself
    (BE-0471); a template is never a target, so it lives outside `targets`. `reservedNamespaces` and
    `doctor` are team-wide rather than per target, so they sit at the top level.

    The hosted multi-tenancy `orgs:` block is a `serve` concern the core does not model (BE-0129);
    `parse_config_dict` drops it before validation so a run reading an org-bearing config keeps
    working, and `bajutsu.serve.orgs` owns the org model.
    """

    defaults: Defaults = Field(default_factory=Defaults)
    targets: dict[str, TargetConfig] = Field(default_factory=dict)
    # Kept as written: `check_templates` checks each template's top-level keys at load, and its
    # nested fields are checked through the targets that extend it (BE-0471).
    templates: dict[str, dict[str, Any]] = Field(default_factory=dict)
    notify: list[NotifyEndpoint] = Field(default_factory=list)
    reserved_namespaces: list[str] = Field(default_factory=list, alias="reservedNamespaces")
    # Configurable doctor thresholds (BE-0024). Always present; defaults to DoctorConfig() (0.9/0.7).
    doctor: DoctorConfig = Field(default_factory=DoctorConfig)

    @model_validator(mode="before")
    @classmethod
    def _merge_defaults(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict):
            return data
        check_templates(data.get("templates"))
        targets = data.get("targets")
        if not isinstance(targets, dict):
            return data
        defaults = data.get("defaults")
        defaults = defaults if isinstance(defaults, dict) else None
        context = info.context if isinstance(info.context, dict) else {}
        config_path = context.get("config_path")
        root = context.get("suite_root")
        merged: dict[str, Any] = {}
        for name, raw in targets.items():
            # A target with neither defaults nor templates stays as written, so its `null` values
            # reach validation as they always have.
            if not isinstance(raw, dict) or (defaults is None and "extends" not in raw):
                merged[name] = raw
                continue
            layers = expand_extends(
                name,
                raw,
                data.get("templates"),
                config_path=config_path if isinstance(config_path, Path) else None,
                root=root if isinstance(root, Path) else None,
            )
            merged[name] = merge_defaults(defaults, *layers)
        return {**data, "targets": merged}
