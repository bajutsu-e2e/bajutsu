"""Configuration — team defaults overlaid by per-app config.

resolve() produces the effective config for one app: the app entry overrides
defaults. `backend` may be a single string or a list (normalized to a list).
`redact` is merged (union). Scenario-level overrides (preconditions) are applied
later by the runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bajutsu import _yaml
from bajutsu.scenario import Redact


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def _as_list(v: Any) -> Any:
    return [v] if isinstance(v, str) else v


class MockServer(_Model):
    cmd: str
    port: int
    stubs: str | None = None


class Defaults(_Model):
    backend: list[str] = Field(default_factory=lambda: ["idb"])
    device: str = "iPhone 15"
    locale: str = "en_US"
    capture: list[str] = Field(
        default_factory=lambda: ["screenshot.after", "elements", "actionLog"]
    )
    redact: Redact = Field(default_factory=Redact)
    secrets: list[str] = Field(default_factory=list)
    reserved_namespaces: list[str] = Field(default_factory=list, alias="reservedNamespaces")

    @field_validator("backend", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return _as_list(v)


class AppConfig(_Model):
    bundle_id: str = Field(alias="bundleId")
    deeplink_scheme: str | None = Field(default=None, alias="deeplinkScheme")
    backend: list[str] | None = None
    device: str | None = None
    locale: str | None = None
    launch_env: dict[str, str] = Field(default_factory=dict, alias="launchEnv")
    launch_args: list[str] = Field(default_factory=list, alias="launchArgs")
    id_namespaces: list[str] = Field(default_factory=list, alias="idNamespaces")
    mock_server: MockServer | None = Field(default=None, alias="mockServer")
    setup: str | None = None
    redact: Redact = Field(default_factory=Redact)
    secrets: list[str] = Field(default_factory=list)

    @field_validator("backend", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return _as_list(v) if v is not None else v


class Config(_Model):
    defaults: Defaults = Field(default_factory=Defaults)
    apps: dict[str, AppConfig] = Field(default_factory=dict)


@dataclass(frozen=True)
class Effective:
    """The resolved config for one app."""

    app: str
    bundle_id: str
    deeplink_scheme: str | None
    backend: list[str]
    device: str
    locale: str
    launch_env: dict[str, str]
    launch_args: list[str]
    id_namespaces: list[str]
    reserved_namespaces: list[str]
    mock_server: MockServer | None
    setup: str | None
    capture: list[str]
    redact: Redact
    secrets: list[str] = field(default_factory=list)


def _merge_redact(base: Redact, over: Redact) -> Redact:
    def union(a: list[str], b: list[str]) -> list[str]:
        return list(dict.fromkeys([*a, *b]))

    return Redact(
        labels=union(base.labels, over.labels),
        headers=union(base.headers, over.headers),
        fields=union(base.fields, over.fields),
    )


def resolve(config: Config, app: str) -> Effective:
    """Resolve the effective config for one app (the app entry overrides defaults)."""
    if app not in config.apps:
        raise KeyError(f"unknown app: {app!r} (define apps.{app} in config)")
    d = config.defaults
    a = config.apps[app]
    return Effective(
        app=app,
        bundle_id=a.bundle_id,
        deeplink_scheme=a.deeplink_scheme,
        backend=a.backend or d.backend,
        device=a.device or d.device,
        locale=a.locale or d.locale,
        launch_env=dict(a.launch_env),
        launch_args=list(a.launch_args),
        id_namespaces=list(a.id_namespaces),
        reserved_namespaces=list(d.reserved_namespaces),
        mock_server=a.mock_server,
        setup=a.setup,
        capture=list(d.capture),
        redact=_merge_redact(d.redact, a.redact),
        secrets=list(dict.fromkeys([*d.secrets, *a.secrets])),
    )


def load_config(text: str) -> Config:
    """Parse a YAML config string."""
    data = _yaml.safe_load(text) or {}
    return Config.model_validate(data)
