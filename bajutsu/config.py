"""Configuration — team defaults overlaid by per-target config.

resolve() produces the effective config for one target: the target entry overrides
defaults. `backend` may be a single string or a list (normalized to a list).
`redact` is merged (union). Scenario-level overrides (preconditions) are applied
later by the runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bajutsu import _yaml, idb_version
from bajutsu.drivers import base
from bajutsu.scenario import Redact


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def _as_list(v: Any) -> Any:
    return [v] if isinstance(v, str) else v


class MockServer(_Model):
    """A mock server that stubs the dependencies the app under test *calls* (`mockServer:` config)."""

    cmd: str
    port: int
    stubs: str | None = None


class LaunchServer(_Model):
    """How to bring up the app's target server (the host behind `baseUrl`) for a run.

    Unlike `mockServer` (which stubs the dependencies the app *calls*), this hosts the app *under
    test* itself — e.g. the static page for `demos/web`, or the inner `serve` the WebUI dogfood
    drives. `run` probes `readyUrl` first: if it already answers it reuses it (started externally),
    else it runs `cmd`, waits on the readiness probe (a condition wait, never a fixed sleep), and
    tears the process down afterwards.
    """

    cmd: str  # shell command that starts the server (run in its own process group)
    ready_url: str | None = Field(default=None, alias="readyUrl")  # probe target; default: baseUrl
    ready_timeout: float = Field(default=30.0, alias="readyTimeout")  # seconds before giving up
    cwd: str | None = None  # working directory (default: the run's cwd)
    env: dict[str, str] = Field(default_factory=dict)  # extra environment for the server
    # Sandbox runtime for an uploaded bundle's `cmd` (BE-0090). `serve --upload-exec=sandbox` runs
    # `cmd` inside a throwaway container instead of on the host; exactly one of these declares how
    # the container is built (enforced at the sandbox decision point, not here — a non-sandbox
    # config legitimately ships neither).
    docker_image: str | None = Field(
        default=None, alias="dockerImage"
    )  # a Docker image reference: [registry/]repo[:tag|@digest]
    dockerfile: str | None = None  # bundle-relative Dockerfile, built via `docker build`
    port: int | None = None  # in-container listen port, published to a loopback host port

    @field_validator("port")
    @classmethod
    def _port_in_range(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 65535):
            raise ValueError("launchServer.port must be in 1..65535")
        return v


class Mailbox(_Model):
    """A generic HTTP mailbox the `email` step polls (`targets.<name>.mailbox`, BE-0046).

    `url` is the inbox endpoint (GET; commonly `${secrets.*}`), `headers` any auth. The optional
    response mapping absorbs a provider's JSON shape without per-provider code: `messages` is a
    dotted path to the message array (empty = the response is the array), and `fields` maps each
    normalized field (`to` / `subject` / `body` / `receivedAt` / `id`) to the provider's key,
    defaulting to the field's own name.
    """

    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    messages: str = ""
    fields: dict[str, str] = Field(default_factory=dict)


class Defaults(_Model):
    """Team-wide defaults under `defaults:`, overlaid by each target (see `resolve`)."""

    backend: list[str] = Field(default_factory=lambda: ["idb"])
    device: str = "iPhone 15"
    locale: str = "en_US"
    capture: list[str] = Field(
        default_factory=lambda: ["screenshot.after", "elements", "actionLog"]
    )
    redact: Redact = Field(default_factory=Redact)
    secrets: list[str] = Field(default_factory=list)
    reserved_namespaces: list[str] = Field(default_factory=list, alias="reservedNamespaces")
    # Expected idb version range (e.g. ">=1.1.8" or ">=1.1.0,<2.0.0"). Environment-level, not
    # per-app: the pin is the same whichever target a scenario drives. `doctor` reports the
    # installed companion against it; None = no pin declared (BE-0005).
    idb_version: str | None = Field(default=None, alias="idbVersion")

    @field_validator("backend", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return _as_list(v)

    @field_validator("idb_version")
    @classmethod
    def _valid_idb_version(cls, v: str | None) -> str | None:
        # Reject a malformed pin at load time (fail loudly, the right place) rather than letting it
        # surface as a crash when `doctor` later compares against it (BE-0005).
        if v is not None and not idb_version.is_valid_spec(v):
            raise ValueError(
                f"invalid idbVersion {v!r}: use a constraint like '>=1.1.8' or '>=1.1.0,<2.0.0'"
            )
        return v


class TargetConfig(_Model):
    """One app's config under `targets.<name>`, overriding `defaults` for that target."""

    # iOS apps identify the target by bundleId; web apps by baseUrl instead. One of the two is
    # required (the validator below) — defaulting bundleId to "" keeps every iOS `eff.bundle_id`
    # call site a plain `str` while letting a web app omit it.
    bundle_id: str = Field(default="", alias="bundleId")
    base_url: str | None = Field(
        default=None, alias="baseUrl"
    )  # web target (e.g. http://host/page)
    # Web backend only: run with a visible (headed) browser instead of headless. iOS ignores it.
    # The `bajutsu run --headed/--no-headed` flag (and the Web UI's "Show browser" toggle) override.
    headless: bool = True
    # How to bring up baseUrl's host for a run (start → readiness probe → teardown). See LaunchServer.
    launch_server: LaunchServer | None = Field(default=None, alias="launchServer")
    deeplink_scheme: str | None = Field(default=None, alias="deeplinkScheme")
    backend: list[str] | None = None
    device: str | None = None
    locale: str | None = None
    launch_env: dict[str, str] = Field(default_factory=dict, alias="launchEnv")
    launch_args: list[str] = Field(default_factory=list, alias="launchArgs")
    # Selector the launch waits for before a run starts (e.g. `{ id: onboarding.start }`). For an app
    # whose first interactive screen is a modal over always-present chrome, the default element-count
    # readiness can return before the modal presents; `readyWhen` makes the gate wait for that screen
    # (a condition wait, no fixed sleep). None keeps the element-count heuristic.
    ready_when: base.Selector | None = Field(default=None, alias="readyWhen")
    id_namespaces: list[str] = Field(default_factory=list, alias="idNamespaces")
    mock_server: MockServer | None = Field(default=None, alias="mockServer")
    mailbox: Mailbox | None = None
    setup: str | None = None
    # Path to the built .app. When set, a run installs it on each device before launch (if
    # missing) — so a freshly-picked/booted simulator works without a manual `simctl install`.
    app_path: str | None = Field(default=None, alias="appPath")
    # Shell command that builds `app_path`. When set, `bajutsu serve` runs it before the
    # scenario if the binary is missing (so the Web UI builds on demand). Run from the run's
    # working directory; e.g. "make -C demos/features sample-build".
    build: str | None = None
    # Directory of this target's scenario *.yaml files. `run` reads them all; `record` writes new
    # ones here. Relative to the run's working directory (like app_path/build).
    scenarios: str | None = None
    # Directory of baseline images for `visual` assertions. Relative to the run's
    # working directory. Overrides the default (baselines/ beside the scenario file).
    baselines: str | None = None
    # Directory of JSON Schema files for `responseSchema` assertions. Relative to the run's
    # working directory. Overrides the default (schemas/ beside the scenario file).
    schemas: str | None = None
    redact: Redact = Field(default_factory=Redact)
    secrets: list[str] = Field(default_factory=list)

    @field_validator("backend", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return _as_list(v) if v is not None else v

    @model_validator(mode="after")
    def _need_target(self) -> TargetConfig:
        # A malformed target entry (neither bundleId nor baseUrl) still fails fast, so dropping
        # bundleId's required-ness for web doesn't silently accept a target-less iOS app.
        if not self.bundle_id and not self.base_url:
            raise ValueError("target needs bundleId (iOS) or baseUrl (web)")
        return self


class OrgConfig(_Model):
    """One tenant under `orgs.<name>` (BE-0015 multi-tenancy).

    Holds the GitHub logins that belong to it (`members`) and/or the GitHub orgs whose members
    belong to it (`github_orgs`), plus the targets it owns. A login or target named in no org falls
    back to the single `default` org, so a config with no `orgs:` block stays single-tenant.
    """

    members: list[str] = Field(default_factory=list)
    github_orgs: list[str] = Field(default_factory=list, alias="githubOrgs")
    targets: list[str] = Field(default_factory=list)


class Config(_Model):
    """A parsed `bajutsu.config.yaml`: team `defaults`, per-target config, and (optional) `orgs`."""

    defaults: Defaults = Field(default_factory=Defaults)
    targets: dict[str, TargetConfig] = Field(default_factory=dict)
    orgs: dict[str, OrgConfig] = Field(default_factory=dict)


# The single tenant every unassigned user and target falls into; keep in sync with serve's
# `_DEFAULT_ORG`.
DEFAULT_ORG = "default"


def org_for_user(config: Config, login: str) -> str:
    """The org whose members list *login*, or `default` if none do."""
    return next((org for org, oc in config.orgs.items() if login in oc.members), DEFAULT_ORG)


def org_for_target(config: Config, target: str) -> str:
    """The org whose targets list *target*, or `default` if none do."""
    return next((org for org, oc in config.orgs.items() if target in oc.targets), DEFAULT_ORG)


def org_for_identity(config: Config, login: str, github_orgs: list[str]) -> str:
    """The org for a user logging in as *login* with the given GitHub *github_orgs* memberships (BE-0015).

    An explicit `members` listing wins; otherwise the first org whose `github_orgs` intersects the
    user's GitHub orgs; otherwise `default`. Resolution is deterministic in config order.
    """
    explicit = org_for_user(config, login)
    if explicit != DEFAULT_ORG:
        return explicit
    user_orgs = set(github_orgs)
    return next(
        (org for org, oc in config.orgs.items() if user_orgs.intersection(oc.github_orgs)),
        DEFAULT_ORG,
    )


def targets_for_org(config: Config, org: str) -> list[str]:
    """The targets belonging to *org*, restricted to targets actually declared under `targets:`.

    An org that lists an undeclared target name doesn't conjure a runnable target. For `default`,
    that's every declared target no org claims.
    """
    if org == DEFAULT_ORG:
        claimed = {a for oc in config.orgs.values() for a in oc.targets}
        return [a for a in config.targets if a not in claimed]
    oc = config.orgs.get(org)
    return [a for a in oc.targets if a in config.targets] if oc else []


@dataclass(frozen=True)
class Effective:
    """The resolved config for one target."""

    target: str
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
    # Generic HTTP mailbox the `email` step polls (`targets.<name>.mailbox`, BE-0046). None = no
    # mailbox configured, so an `email` step fails cleanly.
    mailbox: Mailbox | None = None
    # Built .app to install on each device before launch (if missing). None = manual install.
    app_path: str | None = None
    # Shell command that builds `app_path`; `bajutsu serve` runs it on demand if the binary
    # is missing. None = no on-demand build.
    build: str | None = None
    # Directory of this target's scenario *.yaml files (config-driven `run`/`record`). None =
    # unset (the caller must pass an explicit scenario path).
    scenarios: str | None = None
    # Baseline images directory for `visual` assertions. None = fall back to
    # baselines/ beside the scenario file (or --baselines CLI flag).
    baselines: str | None = None
    # JSON Schema directory for `responseSchema` assertions. None = fall back to
    # schemas/ beside the scenario file (or --schemas CLI flag).
    schemas: str | None = None
    # Web (Playwright) target URL. None for iOS apps (which use bundle_id instead).
    base_url: str | None = None
    # Web (Playwright): run headless (default) or headed (visible browser). iOS ignores it.
    headless: bool = True
    # How to bring up baseUrl's host for the run (start/probe/teardown). None = assume it's running.
    launch_server: LaunchServer | None = None
    # Selector the launch waits for before a run starts (BE: smoke flake). None = the default
    # element-count readiness heuristic.
    ready_when: base.Selector | None = None
    # Expected idb version range (e.g. ">=1.1.8"); `doctor` checks the installed companion against
    # it. None = no pin declared. Environment-level, so resolved straight from defaults (BE-0005).
    idb_version: str | None = None

    def rebased(self, root: Path) -> Effective:
        """A copy with the relative path fields resolved against `root` (a Git checkout, BE-0063).

        The fields `run` / `doctor` read — `scenarios` / `baselines` / `schemas` / `app_path`; listed
        beside the type so a future path field is rebased by adding it here. `build` (a shell command)
        and `setup` (resolved relative to the scenario, not the cwd) are intentionally absent. Local
        configs keep their cwd-relative paths; only a Git source calls this, so the caller's working
        directory no longer has anything to do with the fetched tree.

        Each field is **confined** to the checkout: an absolute or `../` value that would escape `root`
        is rejected (a fetched config can't reach outside its own tree — mirroring the serve-hardening
        path confinement, BE-0051). Raises ValueError on such a field.
        """
        root_resolved = root.resolve()

        def at(field: str, value: str | None) -> str | None:
            if not value:
                return value
            candidate = root / value
            if not candidate.resolve().is_relative_to(root_resolved):
                raise ValueError(f"config field {field!r} escapes the checkout root: {value!r}")
            return str(candidate)

        return replace(
            self,
            scenarios=at("scenarios", self.scenarios),
            baselines=at("baselines", self.baselines),
            schemas=at("schemas", self.schemas),
            app_path=at("appPath", self.app_path),
        )


def _merge_redact(base: Redact, over: Redact) -> Redact:
    def union(a: list[str], b: list[str]) -> list[str]:
        return list(dict.fromkeys([*a, *b]))

    return Redact(
        labels=union(base.labels, over.labels),
        headers=union(base.headers, over.headers),
        fields=union(base.fields, over.fields),
    )


def resolve(config: Config, target: str) -> Effective:
    """Resolve the effective config for one target (the target entry overrides defaults)."""
    if target not in config.targets:
        raise KeyError(f"unknown target: {target!r} (define targets.{target} in config)")
    d = config.defaults
    a = config.targets[target]
    return Effective(
        target=target,
        bundle_id=a.bundle_id,
        base_url=a.base_url,
        headless=a.headless,
        launch_server=a.launch_server,
        ready_when=a.ready_when,
        deeplink_scheme=a.deeplink_scheme,
        backend=a.backend or d.backend,
        device=a.device or d.device,
        locale=a.locale or d.locale,
        launch_env=dict(a.launch_env),
        launch_args=list(a.launch_args),
        id_namespaces=list(a.id_namespaces),
        reserved_namespaces=list(d.reserved_namespaces),
        mock_server=a.mock_server,
        mailbox=a.mailbox,
        setup=a.setup,
        capture=list(d.capture),
        redact=_merge_redact(d.redact, a.redact),
        secrets=list(dict.fromkeys([*d.secrets, *a.secrets])),
        app_path=a.app_path,
        build=a.build,
        scenarios=a.scenarios,
        baselines=a.baselines,
        schemas=a.schemas,
        idb_version=d.idb_version,
    )


def load_config(text: str) -> Config:
    """Parse a YAML config string."""
    data = _yaml.safe_load(text) or {}
    return Config.model_validate(data)
