"""Configuration input schema — the pydantic models a `bajutsu.config.yaml` validates into.

The team `defaults` and per-target `targets.<name>` blocks, their field validators, and the
`Config` root. Resolution (defaults overlaid by target -> `Effective`) lives in the sibling `resolve`
module, which reads this schema and produces the frozen output types in `effective`; nothing
here depends on `resolve` except the one deferred back-reference in `Config`'s validator.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bajutsu.drivers import base
from bajutsu.scenario import DismissAlerts, Redact

# Playwright rendering engines a web target can drive (BE-0076). Chromium is the default,
# preserving today's single-engine behaviour; all three run headless on Linux.
WEB_ENGINES = ("chromium", "firefox", "webkit")


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
    """A mailbox the `email` step polls (`targets.<name>.mailbox`, BE-0046 / BE-0186).

    `kind` selects the transport adapter (`http`, later `imap`) from the mailbox registry, defaulting
    to `http` so a pre-BE-0186 block is unchanged; an unknown `kind` fails closed when the runner
    resolves the mailbox, not here (the deterministic config must not import the registry, BE-0112).
    `url` is the inbox endpoint (GET; commonly `${secrets.*}`), `headers` any auth. The optional
    response mapping absorbs a provider's JSON shape without per-provider code: `messages` is a
    dotted path to the message array (empty = the response is the array), and `fields` maps each
    normalized field (`to` / `subject` / `body` / `receivedAt` / `id`) to the provider's key,
    defaulting to the field's own name.
    """

    kind: str = "http"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    messages: str = ""
    fields: dict[str, str] = Field(default_factory=dict)


class DeviceProvider(_Model):
    """Where a target's devices come from (`targets.<name>.deviceProvider`, BE-0236).

    `kind` selects the provider adapter from the device-provider registry, defaulting to `local` — a
    locally-attached simulator / emulator / device, exactly today's `--udid` path — so an omitted
    block is unchanged. A device-cloud `kind` reserves a device off-host and hands the run its serial
    / endpoint instead. `endpoint` carries that address for the kinds that need one (the `appium` live
    path points at a reserved iOS device's Appium / WebDriver endpoint, BE-0238). Like the mailbox
    `kind`, an unknown value — or a required-but-missing endpoint — fails closed when the run resolves
    the provider, not here: the deterministic config must not import a cloud SDK (BE-0112).
    """

    kind: str = "local"
    endpoint: str | None = None


class XcuitestConfig(_Model):
    """Per-target XCUITest runner config (`targets.<name>.xcuitest`, BE-0019)."""

    test_runner: str | None = Field(default=None, alias="testRunner")
    build: str | None = None
    # Which iOS target the same `xcodebuild` driving layer runs against (BE-0238): `simulator`
    # (the BE-0019 default) or a real `device`. It only selects the `-destination` platform and
    # whether simctl device-prep applies; an unknown value fails closed here at config load.
    device_type: Literal["simulator", "device"] = Field(default="simulator", alias="deviceType")


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
    # Imported lazily so this schema module carries no top-level dependency on `bajutsu.backends`
    # (only the sibling `resolve` module does); the token set is tiny and this validator runs once.
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
    requires: list[str] = Field(default_factory=list)

    @field_validator("backend", mode="before")
    @classmethod
    def _norm(cls, v: Any) -> Any:
        return _as_list(v)

    @field_validator("platform")
    @classmethod
    def _valid_platform(cls, v: str | None) -> str | None:
        return _check_platform(v)


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
    # Web backend only: the device mode a context is created with (BE-0228). "desktop" (default) is
    # the plain desktop context of before; any other value is a Playwright device preset name
    # (`playwright.devices`, e.g. "iPhone 13") that emulates its viewport / touch / scale / user
    # agent. Resolved lazily in the driver so config load never imports Playwright — an unknown
    # preset fails loudly at driver start, not here. Distinct from the top-level `device` (the iOS
    # simulator name), which a web target ignores.
    device_mode: str = Field(default="desktop", alias="deviceMode")
    # How to bring up baseUrl's host for a run (start → readiness probe → teardown). See LaunchServer.
    launch_server: LaunchServer | None = Field(default=None, alias="launchServer")
    deeplink_scheme: str | None = Field(default=None, alias="deeplinkScheme")
    backend: list[str] | None = None
    # Where this target's devices come from (BE-0236). None = the built-in local provider (today's
    # `--udid` path), so an existing target is unchanged; a device-cloud `kind` reserves a device
    # off-host. Validated against the registry at runtime, not here (the core imports no cloud SDK).
    device_provider: DeviceProvider | None = Field(default=None, alias="deviceProvider")
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
        # The derivation lives in the sibling `resolve` module; imported lazily to avoid a
        # schema <-> resolve import cycle at module load.
        from bajutsu.config.resolve import _PLATFORM_IDENTIFIER, _effective_platform

        for name, t in self.targets.items():
            backend = t.backend or self.defaults.backend
            platform = _effective_platform(t, self.defaults, backend)
            identifier = _PLATFORM_IDENTIFIER.get(platform)
            if identifier is not None and not getattr(t, identifier[1]):
                raise ValueError(f"target {name!r} (platform {platform}) needs {identifier[0]}")
        return self
