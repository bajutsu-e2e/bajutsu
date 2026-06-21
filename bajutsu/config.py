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
    # Path to the built .app. When set, a run installs it on each device before launch (if
    # missing) — so a freshly-picked/booted simulator works without a manual `simctl install`.
    app_path: str | None = Field(default=None, alias="appPath")
    # Shell command that builds `app_path`. When set, `bajutsu serve` runs it before the
    # scenario if the binary is missing (so the Web UI builds on demand). Run from the run's
    # working directory; e.g. "make -C demos/features sample-build".
    build: str | None = None
    # Directory of this app's scenario *.yaml files. `run` reads them all; `record` writes new
    # ones here. Relative to the run's working directory (like app_path/build).
    scenarios: str | None = None
    # Directory of baseline images for `visual` assertions. Relative to the run's
    # working directory. Overrides the default (baselines/ beside the scenario file).
    baselines: str | None = None
    redact: Redact = Field(default_factory=Redact)
    secrets: list[str] = Field(default_factory=list)

    @field_validator("backend", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return _as_list(v) if v is not None else v


class OrgConfig(_Model):
    """One tenant (BE-0015 multi-tenancy): the GitHub logins that belong to it and the apps it
    owns. A login or app named in no org falls back to the single `default` org, so a config with
    no `orgs:` block stays single-tenant."""

    members: list[str] = Field(default_factory=list)
    apps: list[str] = Field(default_factory=list)


class Config(_Model):
    defaults: Defaults = Field(default_factory=Defaults)
    apps: dict[str, AppConfig] = Field(default_factory=dict)
    orgs: dict[str, OrgConfig] = Field(default_factory=dict)


# The single tenant every unassigned user and app falls into; keep in sync with serve's
# `_DEFAULT_ORG`.
DEFAULT_ORG = "default"


def org_for_user(config: Config, login: str) -> str:
    """The org whose members list *login*, or `default` if none do."""
    return next((org for org, oc in config.orgs.items() if login in oc.members), DEFAULT_ORG)


def org_for_app(config: Config, app: str) -> str:
    """The org whose apps list *app*, or `default` if none do."""
    return next((org for org, oc in config.orgs.items() if app in oc.apps), DEFAULT_ORG)


def apps_for_org(config: Config, org: str) -> list[str]:
    """The apps belonging to *org*, restricted to apps actually declared under `apps:` (an org that
    lists an undeclared app name doesn't conjure a runnable app). For `default`, that's every
    declared app no org claims."""
    if org == DEFAULT_ORG:
        claimed = {a for oc in config.orgs.values() for a in oc.apps}
        return [a for a in config.apps if a not in claimed]
    oc = config.orgs.get(org)
    return [a for a in oc.apps if a in config.apps] if oc else []


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
    # Built .app to install on each device before launch (if missing). None = manual install.
    app_path: str | None = None
    # Shell command that builds `app_path`; `bajutsu serve` runs it on demand if the binary
    # is missing. None = no on-demand build.
    build: str | None = None
    # Directory of this app's scenario *.yaml files (config-driven `run`/`record`). None =
    # unset (the caller must pass an explicit scenario path).
    scenarios: str | None = None
    # Baseline images directory for `visual` assertions. None = fall back to
    # baselines/ beside the scenario file (or --baselines CLI flag).
    baselines: str | None = None


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
        app_path=a.app_path,
        build=a.build,
        scenarios=a.scenarios,
        baselines=a.baselines,
    )


def load_config(text: str) -> Config:
    """Parse a YAML config string."""
    data = _yaml.safe_load(text) or {}
    return Config.model_validate(data)
