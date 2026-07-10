"""Configuration — team defaults overlaid by per-target config.

resolve() produces the effective config for one target: the target entry overrides
defaults. `backend` may be a single string or a list (normalized to a list).
`redact` is merged (union). Scenario-level overrides (preconditions) are applied
later by the runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bajutsu import _yaml, idb_version
from bajutsu.drivers import base
from bajutsu.scenario import DismissAlerts, Redact

# Playwright rendering engines a web target can drive (BE-0076). Chromium is the default,
# preserving today's single-engine behaviour; all three run headless on Linux.
WEB_ENGINES = ("chromium", "firefox", "webkit")


@dataclass(frozen=True)
class AiConfig:
    """The resolved `ai` block (BE-0047): which provider/model/endpoint/key the AI paths use.

    Lives with the rest of the resolved config (not the AI client) so the deterministic core can
    read the block without importing the periphery AI stack (BE-0112). Every field is optional — an
    absent field falls back to the environment in the AI-client factory, so a config with no `ai:`
    block behaves exactly as before. `key_env` holds the NAME of the env var that carries the key,
    never the key itself.
    """

    provider: str | None = None
    model: str | None = None
    base_url: str | None = None  # self-hosted gateway / proxy for the Anthropic provider
    key_env: str | None = None  # name of the env var holding the API key (never the key)
    effort: str | None = None  # reasoning-effort level (low/medium/high/xhigh/max) where supported
    language: str | None = None  # AI output language for the generated prose (ja/en/auto), BE-0188
    # AI usage/cost ledger (BE-0196). `usage_ledger` is the JSONL path (None = default under runs/,
    # empty string = disabled); `pricing` overrides the shipped per-token rates, keyed by
    # "provider/model" with input/output/cacheWrite/cacheRead (USD per million tokens). Plain dicts,
    # not the ledger's own types, so the deterministic core stays free of the AI stack (BE-0112).
    usage_ledger: str | None = None
    pricing: dict[str, dict[str, float]] | None = None


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


class XcuitestConfig(_Model):
    """Per-target XCUITest runner config (`targets.<name>.xcuitest`, BE-0019)."""

    test_runner: str | None = Field(default=None, alias="testRunner")
    build: str | None = None


class PricingEntry(_Model):
    """Per-token rates for one `(provider, model)` in `ai.pricing` (BE-0196), USD per million tokens.

    Overrides a shipped default. `cacheWrite` / `cacheRead` default to 0 for a provider that does
    not price cache separately, so an entry may name only `input` / `output`.
    """

    input: float
    output: float
    cache_write: float = Field(default=0.0, alias="cacheWrite")
    cache_read: float = Field(default=0.0, alias="cacheRead")


class AiSettings(_Model):
    """The `ai` block (BE-0047) — which provider/model/endpoint/key the AI paths use.

    `defaults.ai` is overridable per `targets.<name>.ai`. Keys never live here: `keyEnv` is the NAME
    of the env var holding the key, read at call time, so a secret never lands in the repo or an
    uploaded bundle. Every field is optional; an unset field falls back to the environment in the
    factory. `extra="forbid"` (from `_Model`) rejects a stray `apiKey:`-style field that would tempt
    a literal key into config.
    """

    # A registered provider name (BE-0104); anthropic is the default. The name is *not* validated
    # here: the deterministic core must not import the AI provider stack (BE-0112), and the registry
    # that owns the valid names lives in the periphery (`bajutsu.ai`). An unknown name fails closed
    # in that registry the first time an AI path resolves the provider, not at config load.
    provider: str | None = None
    model: str | None = None  # override the path's default model
    base_url: str | None = Field(default=None, alias="baseUrl")  # self-hosted gateway / proxy
    key_env: str | None = Field(default=None, alias="keyEnv")  # NAME of the env var (never the key)
    effort: str | None = None  # reasoning-effort level: low/medium/high/xhigh/max (claude-code)
    # AI output language for the model's generated prose (BE-0188): ja | en | auto. `auto` (the
    # default) keeps today's behavior — `record` follows the goal's language, `crawl` stays English.
    # Governs authoring/investigation prose only; never the deterministic run/CI verdict.
    language: str | None = None
    # AI usage/cost ledger (BE-0196), reporting only — never on the run/CI verdict. `usageLedger` is
    # the JSONL ledger path (unset = default under runs/, empty = disabled); `pricing` overrides the
    # shipped per-token rates, keyed by "provider/model" (e.g. "api-key/sonnet").
    usage_ledger: str | None = Field(default=None, alias="usageLedger")
    pricing: dict[str, PricingEntry] | None = None


def _check_platform(v: str | None) -> str | None:
    """Reject an unknown `platform` token at load time, so a typo fails loudly here.

    `None` means "derive the platform from the backend", so it passes through (BE-0009 Slice 4).
    """
    if v is None:
        return v
    from bajutsu.backends import PLATFORMS

    if v not in PLATFORMS:
        raise ValueError(f"invalid platform {v!r}: use one of {', '.join(PLATFORMS)}")
    return v


class DoctorConfig(_Model):
    """Configurable thresholds for ``bajutsu doctor``'s id-coverage grading (BE-0024).

    ``idCoverageOk`` is the minimum coverage to be eligible for "Ready"; ``idCoverageFail``
    is the ceiling below which the grade drops to "Blocked". Both must be in [0, 1] with
    ok >= fail.
    """

    id_coverage_ok: float = Field(default=0.9, alias="idCoverageOk")
    id_coverage_fail: float = Field(default=0.7, alias="idCoverageFail")

    @model_validator(mode="after")
    def _ok_above_fail(self) -> DoctorConfig:
        if self.id_coverage_ok < self.id_coverage_fail:
            raise ValueError(
                f"doctor.idCoverageOk ({self.id_coverage_ok}) must be >= "
                f"doctor.idCoverageFail ({self.id_coverage_fail})"
            )
        return self

    @field_validator("id_coverage_ok", "id_coverage_fail")
    @classmethod
    def _in_unit_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"doctor threshold must be in [0, 1], got {v}")
        return v


_NOTIFY_EVENTS = frozenset({"failure", "change", "recovery", "always", "start"})


class NotifyEndpoint(_Model):
    """One webhook notification sink (`notify:` list entry, BE-0099)."""

    format: str = "slack"
    url: str
    on: list[str] = Field(default_factory=lambda: ["failure"])
    targets: list[str] = Field(default_factory=list)

    @field_validator("format")
    @classmethod
    def _known_format(cls, v: str) -> str:
        if v not in ("slack",):
            raise ValueError(f"unknown notify format {v!r}: use 'slack'")
        return v

    @field_validator("on", mode="before")
    @classmethod
    def _norm_on(cls, v: Any) -> Any:
        return _as_list(v)

    @model_validator(mode="after")
    def _known_events(self) -> NotifyEndpoint:
        for event in self.on:
            if event not in _NOTIFY_EVENTS:
                raise ValueError(
                    f"unknown notify event {event!r}: "
                    f"use any of {', '.join(sorted(_NOTIFY_EVENTS))}"
                )
        return self


class Defaults(_Model):
    """Team-wide defaults under `defaults:`, overlaid by each target (see `resolve`)."""

    backend: list[str] = Field(default_factory=lambda: ["idb"])
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
    # Expected idb version range (e.g. ">=1.1.8" or ">=1.1.0,<2.0.0"). Environment-level, not
    # per-app: the pin is the same whichever target a scenario drives. `doctor` reports the
    # installed companion against it; None = no pin declared (BE-0005).
    idb_version: str | None = Field(default=None, alias="idbVersion")
    visual_compare: Literal["exact", "pixelmatch"] | None = Field(
        default=None, alias="visualCompare"
    )
    # Team-wide capability tokens every target requires of the worker that runs it (BE-0166), e.g.
    # `[ios18, ipad]`. On the hosted backend these route the job to a worker advertising them; a
    # per-target `requires` adds to (never replaces) this. Empty = only the platform axis routes.
    requires: list[str] = Field(default_factory=list)

    @field_validator("backend", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return _as_list(v)

    @field_validator("platform")
    @classmethod
    def _valid_platform(cls, v: str | None) -> str | None:
        return _check_platform(v)

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

    # The platform this target runs on (ios / android / web). None derives it from the backend
    # (BE-0009 Slice 4), so a config written before this field is unchanged; an explicit value is
    # authoritative and selects which identifier below is required.
    platform: str | None = None
    # Each platform identifies the target by its own handle: iOS by bundleId, web by baseUrl, Android
    # by package. The required one is validated for the resolved platform (see Config below); defaulting
    # the string ones to "" keeps every `eff.bundle_id` / `eff.package` call site a plain `str`.
    bundle_id: str = Field(default="", alias="bundleId")
    base_url: str | None = Field(
        default=None, alias="baseUrl"
    )  # web target (e.g. http://host/page)
    package: str = Field(default="", alias="package")  # Android target (e.g. com.example.app)
    # Android only: runtime permissions granted up front (`pm grant`) before launch, so a permission
    # prompt never blocks a scenario (BE-0210). App-specific, so it lives in config, not the driver.
    grant_permissions: list[str] = Field(default_factory=list, alias="grantPermissions")
    # Web backend only: run with a visible (headed) browser instead of headless. iOS ignores it.
    # The `bajutsu run --headed/--no-headed` flag (and the Web UI's "Show browser" toggle) override.
    headless: bool = True
    # Web backend only: which Playwright engine to drive — chromium (default) / firefox / webkit.
    # iOS ignores it. The `bajutsu run/record --browser <engine>` flag overrides per run (BE-0076).
    browser: str = "chromium"
    # How to bring up baseUrl's host for a run (start → readiness probe → teardown). See LaunchServer.
    launch_server: LaunchServer | None = Field(default=None, alias="launchServer")
    deeplink_scheme: str | None = Field(default=None, alias="deeplinkScheme")
    backend: list[str] | None = None
    device: str | None = None
    locale: str | None = None
    # Capability tokens this target requires of the worker that runs it (BE-0166), added to the
    # team-wide `defaults.requires`. On the hosted backend a job is routed only to a worker that
    # advertises all of them (e.g. `ios18`, `ipad`); ignored by local single-worker runs.
    requires: list[str] = Field(default_factory=list)
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
    # working directory; e.g. "make -C demos/showcase swiftui-build".
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
    # Directory of golden JSON files for `golden` assertions (BE-0006). Relative to the run's
    # working directory. Overrides the default (goldens/ beside the scenario file).
    goldens: str | None = None
    redact: Redact = Field(default_factory=Redact)
    secrets: list[str] = Field(default_factory=list)
    # XCUITest runner config (BE-0019): prebuilt test runner path and/or build command.
    xcuitest: XcuitestConfig | None = None
    # Per-target AI provider/model/endpoint/key (BE-0047), overriding defaults.ai field by field.
    ai: AiSettings | None = None
    # Per-target webhook notification override (BE-0099). None inherits the top-level `notify:`.
    notify: list[NotifyEndpoint] | None = None
    visual_compare: Literal["exact", "pixelmatch"] | None = Field(
        default=None, alias="visualCompare"
    )
    # Per-app defaults for the run-behavior knobs that otherwise live per-scenario or on a CLI flag
    # (BE-0177). Each sits *between* the per-scenario value and the built-in default: the flag still
    # overrides for one run, then the scenario's own value, then this, then the built-in — mirroring
    # `--headed`/`headless`. None = unset (fall through to the built-in default).
    dismiss_alerts: DismissAlerts | None = Field(default=None, alias="dismissAlerts")
    erase: bool | None = None  # default for preconditions.erase (built-in: off)
    network: bool | None = None  # collect the app's network exchanges (built-in: on)

    @field_validator("backend", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return _as_list(v) if v is not None else v

    @field_validator("platform")
    @classmethod
    def _valid_platform(cls, v: str | None) -> str | None:
        return _check_platform(v)

    @field_validator("browser")
    @classmethod
    def _valid_browser(cls, v: str) -> str:
        # Reject a typo'd engine at load time (the loud, right place) rather than letting it surface
        # as an AttributeError when the driver does `getattr(pw, engine)` mid-run (BE-0076).
        if v not in WEB_ENGINES:
            raise ValueError(f"invalid browser {v!r}: use one of {', '.join(WEB_ENGINES)}")
        return v

    @field_validator("ready_when")
    @classmethod
    def _valid_ready_when(cls, v: base.Selector | None) -> base.Selector | None:
        # A `readyWhen` id/idMatches candidate list is checked the same way a scenario-step selector
        # is — empty/blank/non-canonical-first fails loudly at load, not silently (BE-0221).
        if v is not None:
            base.validate_id_candidates("id", v.get("id"))
            base.validate_id_candidates("idMatches", v.get("idMatches"))
        return v

    @model_validator(mode="after")
    def _need_target(self) -> TargetConfig:
        # A malformed target entry (no identifier at all) still fails fast. The platform-aware check
        # that the *right* identifier is present for the resolved platform lives on Config (it needs
        # defaults to derive the platform).
        if not self.bundle_id and not self.base_url and not self.package:
            raise ValueError("target needs bundleId (iOS), baseUrl (web), or package (Android)")
        return self


class Config(_Model):
    """A parsed `bajutsu.config.yaml`: team `defaults` and per-target config.

    The hosted multi-tenancy `orgs:` block is a `serve` concern the core does not model (BE-0129);
    `parse_config_dict` drops it before validation so a run reading an org-bearing config keeps
    working, and `bajutsu.serve.orgs` owns the org model.
    """

    defaults: Defaults = Field(default_factory=Defaults)
    targets: dict[str, TargetConfig] = Field(default_factory=dict)
    notify: list[NotifyEndpoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def _targets_carry_their_platform_identifier(self) -> Config:
        # Each target must carry the identifier its resolved platform needs (iOS bundleId / web
        # baseUrl / Android package). Validated here, not on TargetConfig, because deriving the
        # platform from the backend needs `defaults` (BE-0009 Slice 4). Backward compatible: a config
        # with no `platform` derives it from the backend, so existing iOS/web targets already pass.
        for name, t in self.targets.items():
            backend = t.backend or self.defaults.backend
            platform = _effective_platform(t, self.defaults, backend)
            identifier = _PLATFORM_IDENTIFIER.get(platform)
            if identifier is not None and not getattr(t, identifier[1]):
                raise ValueError(f"target {name!r} (platform {platform}) needs {identifier[0]}")
        return self


@dataclass(frozen=True)
class IosConfig:
    """iOS (idb / XCUITest) target knobs (BE-0126). On `Effective` only when `platform == "ios"`."""

    bundle_id: str = ""
    deeplink_scheme: str | None = None
    # Built .app to install on each device before launch (if missing). None = manual install.
    app_path: str | None = None
    # Shell command that builds `app_path`; `bajutsu serve` runs it on demand if the binary is
    # missing. None = no on-demand build.
    build: str | None = None
    # XCUITest runner config (BE-0019): prebuilt test runner path and/or build command.
    xcuitest: XcuitestConfig | None = None
    # Expected idb version range (e.g. ">=1.1.8"); `doctor` checks the installed companion against
    # it. None = no pin declared. Environment-level, so resolved straight from defaults (BE-0005).
    idb_version: str | None = None


@dataclass(frozen=True)
class WebConfig:
    """Web (Playwright) target knobs (BE-0126). On `Effective` only when `platform == "web"`."""

    # Web (Playwright) target URL. None when unset (the config gate then fails cleanly).
    base_url: str | None = None
    # Run headless (default) or headed (visible browser). The `--headed` flag overrides per run.
    headless: bool = True
    # The rendering engine to drive — chromium (default) / firefox / webkit. The `--browser` flag
    # overrides per run (BE-0076).
    browser: str = "chromium"


@dataclass(frozen=True)
class AndroidConfig:
    """Android (adb) target knobs (BE-0126 / BE-0007). On `Effective` only when `platform` is android."""

    # Android target identifier (peer of iOS bundle_id / web base_url). "" when unset.
    package: str = ""
    # Built .apk to install on each device before launch (if missing). None = manual install.
    app_path: str | None = None
    # Shell command that builds `app_path`; `bajutsu serve` runs it on demand if the binary is
    # missing. None = no on-demand build.
    build: str | None = None
    # Runtime permissions granted up front (`pm grant`) at lease time, so a permission prompt never
    # blocks a scenario (BE-0210). Empty = grant nothing.
    grant_permissions: list[str] = field(default_factory=list)


# The resolved platform-specific config, keyed by platform (BE-0126). The concrete type *is* the
# discriminator, so a caller must narrow (isinstance / match) before reading a platform's knobs —
# reading a web field on an iOS target is a type error, not a silently-meaningless value.
PlatformConfig = IosConfig | WebConfig | AndroidConfig


@dataclass(frozen=True)
class Effective:
    """The resolved config for one target."""

    target: str
    # The platform-specific knobs (BE-0126); its concrete type is the platform discriminator.
    platform_config: PlatformConfig
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
    # Resolved AI provider/model/endpoint/key (BE-0047), passed to the AI factory. None = env-only.
    ai: AiConfig | None = None
    # Generic HTTP mailbox the `email` step polls (`targets.<name>.mailbox`, BE-0046). None = no
    # mailbox configured, so an `email` step fails cleanly.
    mailbox: Mailbox | None = None
    # Directory of this target's scenario *.yaml files (config-driven `run`/`record`). None =
    # unset (the caller must pass an explicit scenario path).
    scenarios: str | None = None
    # Baseline images directory for `visual` assertions. None = fall back to
    # baselines/ beside the scenario file (or --baselines CLI flag).
    baselines: str | None = None
    # JSON Schema directory for `responseSchema` assertions. None = fall back to
    # schemas/ beside the scenario file (or --schemas CLI flag).
    schemas: str | None = None
    # Golden JSON directory for `golden` assertions (BE-0006). None = fall back to
    # goldens/ beside the scenario file (or --goldens CLI flag).
    goldens: str | None = None
    # How to bring up baseUrl's host for the run (start/probe/teardown). None = assume it's running.
    launch_server: LaunchServer | None = None
    # Selector the launch waits for before a run starts (BE: smoke flake). None = the default
    # element-count readiness heuristic.
    ready_when: base.Selector | None = None
    # Configurable doctor id-coverage thresholds (BE-0024). Teams with many decorative elements
    # can tune thresholds (often lowering ok and/or fail for leniency) without changing the tool.
    doctor_ok_coverage: float = 0.9
    doctor_fail_coverage: float = 0.7
    # Webhook notification sinks (BE-0099). Empty when no `notify:` is configured.
    notify: list[NotifyEndpoint] = field(default_factory=list)
    visual_compare: str = "exact"
    # Capability tokens the worker running this target must advertise (BE-0166): the union of
    # `defaults.requires` and the target's own `requires`. Empty when neither is set (only the
    # platform axis routes). Consumed by the hosted job router, not the deterministic run.
    requires: list[str] = field(default_factory=list)
    # Per-app run-behavior defaults (BE-0177), resolved from the target: the layer the run consults
    # when neither a CLI flag nor the scenario sets the value. `dismiss_alerts` None = built-in on with
    # the default instruction; `erase` / `network` are the concrete built-in defaults when unset.
    dismiss_alerts: DismissAlerts | None = None
    erase: bool = False
    network: bool = True

    @property
    def platform(self) -> str:
        """The resolved platform (ios / android / web), derived from the sub-config's type (BE-0126)."""
        if isinstance(self.platform_config, IosConfig):
            return "ios"
        if isinstance(self.platform_config, WebConfig):
            return "web"
        return "android"

    def rebased(self, root: Path) -> Effective:
        """A copy with the relative path fields resolved against `root` (a Git checkout, BE-0063).

        The common path fields — `scenarios` / `baselines` / `schemas` / `goldens` — and the iOS or
        Android sub-config's `app_path` are rebased; a future path field is rebased by adding it here. `build`
        (a shell command) and `setup` (resolved relative to the scenario, not the cwd) are
        intentionally absent. Local configs keep their cwd-relative paths; only a Git source calls
        this, so the caller's working directory no longer has anything to do with the fetched tree.

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

        common = replace(
            self,
            scenarios=at("scenarios", self.scenarios),
            baselines=at("baselines", self.baselines),
            schemas=at("schemas", self.schemas),
            goldens=at("goldens", self.goldens),
        )
        if isinstance(self.platform_config, AndroidConfig):
            # The Android sub-config's only rebasable path is the APK (BE-0007).
            android = self.platform_config
            return replace(
                common,
                platform_config=replace(android, app_path=at("appPath", android.app_path)),
            )
        if not isinstance(self.platform_config, IosConfig):
            return common  # only the iOS / Android sub-configs carry rebasable path fields

        ios = self.platform_config
        rebased_xcuitest = ios.xcuitest
        if rebased_xcuitest is not None and rebased_xcuitest.test_runner is not None:
            rebased_xcuitest = XcuitestConfig.model_validate(
                {
                    "testRunner": at("xcuitest.testRunner", rebased_xcuitest.test_runner),
                    "build": rebased_xcuitest.build,
                }
            )
        return replace(
            common,
            platform_config=replace(
                ios, app_path=at("appPath", ios.app_path), xcuitest=rebased_xcuitest
            ),
        )


def require_ios(eff: Effective) -> IosConfig:
    """The iOS sub-config, narrowed for the type checker, or a loud failure (BE-0126).

    For code already committed to an iOS-only path (the iOS/XCUITest environment, the idb doctor
    probe): it narrows the platform union to `IosConfig` and fails fast rather than silently reading
    a default if a non-iOS target ever reaches it. Code that has *not* committed to a platform must
    narrow with `isinstance` / `match` instead — reading a platform's knobs off `platform_config`
    without narrowing is a type error, which is the point of the split.
    """
    cfg = eff.platform_config
    if not isinstance(cfg, IosConfig):
        raise TypeError(f"target {eff.target!r} is not an iOS target (platform {eff.platform})")
    return cfg


def require_web(eff: Effective) -> WebConfig:
    """The web sub-config, narrowed for the type checker, or a loud failure (BE-0126).

    The web counterpart of `require_ios`, for code already on a web-only path (the web environment,
    the Playwright doctor probe).
    """
    cfg = eff.platform_config
    if not isinstance(cfg, WebConfig):
        raise TypeError(f"target {eff.target!r} is not a web target (platform {eff.platform})")
    return cfg


def require_android(eff: Effective) -> AndroidConfig:
    """The Android sub-config, narrowed for the type checker, or a loud failure (BE-0126 / BE-0007).

    The Android counterpart of `require_ios` / `require_web`, for code already on an adb-only path
    (the `AndroidEnvironment` lifecycle).
    """
    cfg = eff.platform_config
    if not isinstance(cfg, AndroidConfig):
        raise TypeError(f"target {eff.target!r} is not an Android target (platform {eff.platform})")
    return cfg


# "Soft" per-platform accessors (BE-0126): the platform's knob, or a safe default for another
# platform. Unlike `require_ios` / `require_web`, these don't fail — they're for code that reads a
# platform-specific value defensively across platforms (a common-core field whose meaningful value
# only exists on one platform, e.g. launchServer's baseUrl probe; or a config gate that inspects
# every platform's handle). One definition each, shared by every layer, instead of an inline
# `isinstance` at each call site.
def web_base_url(eff: Effective) -> str | None:
    """The web target's base URL, or None for a non-web target."""
    return eff.platform_config.base_url if isinstance(eff.platform_config, WebConfig) else None


def web_engine(eff: Effective) -> str:
    """The web target's rendering engine, or the chromium default for a non-web target."""
    return eff.platform_config.browser if isinstance(eff.platform_config, WebConfig) else "chromium"


def ios_bundle_id(eff: Effective) -> str:
    """The iOS target's bundle id, or "" for a non-iOS target."""
    return eff.platform_config.bundle_id if isinstance(eff.platform_config, IosConfig) else ""


def android_package(eff: Effective) -> str:
    """The Android target's package, or "" for a non-Android target."""
    return eff.platform_config.package if isinstance(eff.platform_config, AndroidConfig) else ""


def idb_version_pin(eff: Effective) -> str | None:
    """The iOS target's declared idb version range (`defaults.idbVersion`), or None when unpinned."""
    return eff.platform_config.idb_version if isinstance(eff.platform_config, IosConfig) else None


def _merge_redact(base: Redact, over: Redact) -> Redact:
    def union(a: list[str], b: list[str]) -> list[str]:
        return list(dict.fromkeys([*a, *b]))

    return Redact(
        labels=union(base.labels, over.labels),
        headers=union(base.headers, over.headers),
        fields=union(base.fields, over.fields),
        unmaskHeaders=union(base.unmask_headers, over.unmask_headers),
    )


def _merge_ai(base: AiSettings | None, over: AiSettings | None) -> AiConfig | None:
    """Merge defaults.ai with the target's ai, field by field (target wins). None when neither set."""
    if base is None and over is None:
        return None
    b = base or AiSettings()
    o = over or AiSettings()
    pricing = o.pricing if o.pricing is not None else b.pricing
    return AiConfig(
        provider=o.provider or b.provider,
        model=o.model or b.model,
        base_url=o.base_url or b.base_url,
        key_env=o.key_env or b.key_env,
        effort=o.effort or b.effort,
        language=o.language or b.language,
        usage_ledger=o.usage_ledger if o.usage_ledger is not None else b.usage_ledger,
        # `by_alias` emits the camelCase rate keys (`cacheWrite`/`cacheRead`) the ledger reads, so the
        # key names live only on `PricingEntry` — not restated here (keeps the AI stack out of core).
        pricing=(
            {k: v.model_dump(by_alias=True) for k, v in pricing.items()}
            if pricing is not None
            else None
        ),
    )


def _platform_for_backend(backend: list[str]) -> str | None:
    """The platform a backend list implies, or None.

    A token may be a platform alias (`web`) or a bare actuator (`playwright`), so it is expanded to
    an actuator before the reverse lookup.
    """
    from bajutsu.backends import platform_of, resolve_actuators

    actuators = resolve_actuators(backend)
    return platform_of(actuators[0]) if actuators else None


def _effective_platform(a: TargetConfig, d: Defaults, backend: list[str]) -> str:
    """The target's platform (BE-0009 Slice 4), preserving the pre-`platform` behavior.

    Precedence: an explicit `platform` (target then defaults) wins; else an explicit *target* backend
    implies it; else the identifier the target carries (baseUrl -> web, package -> android, bundleId
    -> ios), so a web target written as just `baseUrl` is web even though the default backend is idb;
    else the (possibly defaulted) backend; else `ios`.
    """
    explicit = a.platform or d.platform
    if explicit:
        return explicit
    if a.backend is not None:
        from_backend = _platform_for_backend(a.backend)
        if from_backend:
            return from_backend
    if a.base_url:
        return "web"
    if a.package:
        return "android"
    if a.bundle_id:
        return "ios"
    return _platform_for_backend(backend) or "ios"


# The identifier each platform requires on its target, as (config field name, TargetConfig attr);
# `fake` (and any platform absent here) needs none. Used by Config validation to reject a target
# carrying the wrong handle for its platform.
_PLATFORM_IDENTIFIER: dict[str, tuple[str, str]] = {
    "ios": ("bundleId", "bundle_id"),
    "web": ("baseUrl", "base_url"),
    "android": ("package", "package"),
}


def _platform_config(platform: str, a: TargetConfig, d: Defaults) -> PlatformConfig:
    """Build the platform-specific sub-config for the resolved platform (BE-0126).

    iOS knobs come from the target except `idb_version`, an environment-level default (BE-0005).
    """
    if platform == "web":
        return WebConfig(base_url=a.base_url, headless=a.headless, browser=a.browser)
    if platform == "android":
        return AndroidConfig(
            package=a.package,
            app_path=a.app_path,
            build=a.build,
            grant_permissions=a.grant_permissions,
        )
    return IosConfig(
        bundle_id=a.bundle_id,
        deeplink_scheme=a.deeplink_scheme,
        app_path=a.app_path,
        build=a.build,
        xcuitest=a.xcuitest,
        idb_version=d.idb_version,
    )


def resolve(config: Config, target: str) -> Effective:
    """Resolve the effective config for one target (the target entry overrides defaults)."""
    if target not in config.targets:
        raise KeyError(f"unknown target: {target!r} (define targets.{target} in config)")
    d = config.defaults
    a = config.targets[target]
    backend = a.backend or d.backend
    return Effective(
        target=target,
        platform_config=_platform_config(_effective_platform(a, d, backend), a, d),
        launch_server=a.launch_server,
        ready_when=a.ready_when,
        backend=backend,
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
        requires=list(dict.fromkeys([*d.requires, *a.requires])),
        ai=_merge_ai(d.ai, a.ai),
        scenarios=a.scenarios,
        baselines=a.baselines,
        schemas=a.schemas,
        goldens=a.goldens,
        doctor_ok_coverage=d.doctor.id_coverage_ok,
        doctor_fail_coverage=d.doctor.id_coverage_fail,
        notify=a.notify if a.notify is not None else list(config.notify),
        visual_compare=a.visual_compare or d.visual_compare or "exact",
        dismiss_alerts=a.dismiss_alerts,
        erase=a.erase if a.erase is not None else False,
        network=a.network if a.network is not None else True,
    )


def parse_config_dict(data: dict[str, Any]) -> Config:
    """Validate an already-parsed config document into a `Config`.

    Top-level `orgs:` and `ui:` keys are dropped before validation: the hosted multi-tenancy org
    model (BE-0129) and the serve UI's `ui.default_theme` (BE-0191) are `serve` concerns the
    deterministic core does not model, and a run in the hosted topology legitimately reads a config
    carrying them, so the core must ignore the keys rather than reject them under `extra="forbid"`.
    `bajutsu.serve.orgs` / `bajutsu.serve.themes` parse those blocks separately. Every other unknown
    key still fails loudly, preserving the typo guard.
    """
    # Only drop from an actual mapping; a non-dict document (a YAML scalar/list) flows unchanged
    # into model_validate, which raises a pydantic ValidationError (a ValueError) rather than the
    # AttributeError a `.items()` on a scalar would throw and escape the callers' handling.
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k not in ("orgs", "ui")}
    return Config.model_validate(data)


def load_config(text: str) -> Config:
    """Parse a YAML config string into a `Config` (see `parse_config_dict`)."""
    return parse_config_dict(_yaml.safe_load(text) or {})
