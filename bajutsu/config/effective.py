"""Resolved config output types — the frozen shape `resolve` produces for one target.

`Effective` is the deterministic core's view of one target's config: the platform-specific knobs
narrowed behind the `PlatformConfig` union, plus the resolved common-core fields. Its cohesive
field clusters are grouped into their own frozen sub-records (`EvidenceDirs` / `RunDefaults` /
`DoctorThresholds`), the same way `platform_config` narrows the platform axis (BE-0252). The
merge/derivation that builds an `Effective` from the input `schema` lives in the sibling `resolve`
module; nothing here depends on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from bajutsu.config.schema import (
    DeviceProvider,
    LaunchServer,
    Mailbox,
    MockServer,
    NotifyEndpoint,
    XcuitestConfig,
)
from bajutsu.drivers import base
from bajutsu.scenario import AlertHandling, Interrupt, Redact


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


@dataclass(frozen=True)
class IosConfig:
    """iOS (XCUITest) target knobs (BE-0126). On `Effective` only when `platform == "ios"`."""

    bundle_id: str = ""
    deeplink_scheme: str | None = None
    # Built .app to install on each device before launch (if missing). None = manual install.
    app_path: str | None = None
    # Shell command that builds `app_path`; `bajutsu serve` runs it on demand if the binary is
    # missing. None = no on-demand build.
    build: str | None = None
    # XCUITest runner config (BE-0019): prebuilt test runner path and/or build command.
    xcuitest: XcuitestConfig | None = None


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
    # The device mode a browser context is created with (BE-0228): "desktop" (default, unchanged) or
    # a Playwright device preset name (e.g. "iPhone 13") emulating a mobile viewport / touch / user
    # agent. Resolved against `playwright.devices` in the driver, lazily.
    device_mode: str = "desktop"


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
class EvidenceDirs:
    """The target's evidence directory overrides (BE-0252 grouping of `Effective`).

    Each is the target's directory for that evidence kind (config-driven `run`/`record`). None
    means "unset": scenarios then requires an explicit path, and each of baselines / schemas /
    goldens falls back to the directory beside the scenario file (or the matching `--<kind>` flag).
    """

    scenarios: str | None = None
    baselines: str | None = None
    schemas: str | None = None
    goldens: str | None = None


@dataclass(frozen=True)
class RunDefaults:
    """Per-app run-behavior defaults (BE-0177), grouped out of `Effective` (BE-0252).

    The layer the run consults when neither a CLI flag nor the scenario sets the value.
    `alert_handling` None = built-in on with the default instruction; `erase` / `network` are the
    concrete built-in defaults when unset.
    """

    alert_handling: AlertHandling | None = None
    erase: bool = False
    network: bool = True
    # App-wide interstitial-screen handlers (BE-0314), prepended to a scenario's own `interrupts`.
    # Empty when the target config declares none.
    interrupts: list[Interrupt] = field(default_factory=list)


@dataclass(frozen=True)
class DoctorThresholds:
    """Configurable doctor id-coverage thresholds (BE-0024), grouped out of `Effective` (BE-0252).

    Teams with many decorative elements can tune thresholds (often lowering ok and/or fail for
    leniency) without changing the tool.
    """

    ok_coverage: float = 0.9
    fail_coverage: float = 0.7


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
    # Where this target's devices come from (BE-0236). None = the built-in local provider (today's
    # locally-attached `--udid` path); a device-cloud `kind` reserves a device off-host. Resolved by
    # `acquire_device` against the provider registry — off the deterministic verdict path.
    device_provider: DeviceProvider | None = None
    # Evidence directory overrides — scenarios / baselines / schemas / goldens (BE-0252).
    evidence_dirs: EvidenceDirs = field(default_factory=EvidenceDirs)
    # How to bring up baseUrl's host for the run (start/probe/teardown). None = assume it's running.
    launch_server: LaunchServer | None = None
    # Selector the launch waits for before a run starts (BE: smoke flake). None = the default
    # element-count readiness heuristic.
    ready_when: base.Selector | None = None
    # Configurable doctor id-coverage thresholds (BE-0024 / BE-0252).
    doctor_thresholds: DoctorThresholds = field(default_factory=DoctorThresholds)
    # Webhook notification sinks (BE-0099). Empty when no `notify:` is configured.
    notify: list[NotifyEndpoint] = field(default_factory=list)
    visual_compare: str = "exact"
    # Capability tokens the worker running this target must advertise (BE-0166): the union of
    # `defaults.requires` and the target's own `requires`. Empty when neither is set (only the
    # platform axis routes). Consumed by the hosted job router, not the deterministic run.
    requires: list[str] = field(default_factory=list)
    # Per-app run-behavior defaults (BE-0177 / BE-0252): the layer the run consults when neither a
    # CLI flag nor the scenario sets the value.
    run_defaults: RunDefaults = field(default_factory=RunDefaults)

    @property
    def platform(self) -> str:
        """The resolved platform (ios / android / web), derived from the sub-config's type (BE-0126)."""
        if isinstance(self.platform_config, IosConfig):
            return "ios"
        if isinstance(self.platform_config, WebConfig):
            return "web"
        return "android"

    def rebased(self, root: Path, *, confine: bool = True) -> Effective:
        """A copy with the relative path fields resolved against `root`.

        The common path fields — `scenarios` / `baselines` / `schemas` / `goldens` — and the iOS or
        Android sub-config's `app_path` are rebased; a future path field is rebased by adding it here. `build`
        (a shell command) and `setup` (resolved relative to the scenario, not the cwd) are
        intentionally absent. Called for a Git checkout (BE-0063), for an uploaded bundle, and — with
        `confine=False`, `root` the config file's own directory — for a local config (BE-0242), so the
        caller's working directory no longer decides where a config's paths point.

        `confine` gates the escape check: when true (an untrusted source — a fetched Git config or an
        uploaded bundle), an absolute or `../` value that would leave `root` raises ValueError, mirroring
        the serve-hardening path confinement (BE-0051). A local file is operator-trusted (BE-0121), so it
        passes `confine=False` and may point at a sibling outside its own directory.
        """
        root_resolved = root.resolve()

        def at(field: str, value: str | None) -> str | None:
            if not value:
                return value
            candidate = root / value
            if confine and not candidate.resolve().is_relative_to(root_resolved):
                raise ValueError(f"config field {field!r} escapes the checkout root: {value!r}")
            return str(candidate)

        common = replace(
            self,
            evidence_dirs=replace(
                self.evidence_dirs,
                scenarios=at("scenarios", self.evidence_dirs.scenarios),
                baselines=at("baselines", self.evidence_dirs.baselines),
                schemas=at("schemas", self.evidence_dirs.schemas),
                goldens=at("goldens", self.evidence_dirs.goldens),
            ),
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
                    "deviceType": rebased_xcuitest.device_type,
                }
            )
        return replace(
            common,
            platform_config=replace(
                ios, app_path=at("appPath", ios.app_path), xcuitest=rebased_xcuitest
            ),
        )
