"""One app's block under `targets.<name>` — where every per-app difference is declared."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import AliasChoices, Field, field_validator, model_validator

from bajutsu.common.deprecations import reject_renamed_key
from bajutsu.common.drivers import base
from bajutsu.common.scenario import AfterRule, Interrupt, Redact, Step, SystemAlertHandlingField

from ._functions import _as_list, _check_platform
from ._model import _Model
from .ai_settings import AiSettings
from .device_provider import DeviceProvider
from .launch_server import LaunchServer
from .mailbox import Mailbox
from .mock_server import MockServer
from .notify_endpoint import NotifyEndpoint
from .xcuitest_config import XcuitestConfig

# Playwright rendering engines a web target can drive (BE-0076). Chromium is the default,
# preserving today's single-engine behaviour; all three run headless on Linux.
WEB_ENGINES = ("chromium", "firefox", "webkit")


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
    # Android only: ask the device to measure each opted-in view's own `View.getZ()` (BE-0355), which
    # `elements.json` reports as `nativeZ`. Off by default because the walk that answers it covers
    # every node on every read — 20-100ms — and returns nothing at all unless the app under test links
    # `BajutsuAndroid` and opts views in, which is why the cost belongs to the targets that read the
    # value rather than to every Android run (BE-0407 unit 18).
    native_z: bool = Field(default=False, alias="nativeZ")
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
    # Which registered batch provider serve's per-scenario fan-out submits this target's cloud runs
    # to (BE-0336 Unit 3). None = the target runs on local devices; a value (e.g. "devicefarm") names
    # a kind in the batch-provider registry. Validated at runtime, not here, so the deterministic
    # core imports no cloud SDK (BE-0112). Distinct from `deviceProvider` (the live-device topology).
    cloud_batch: str | None = Field(default=None, alias="cloudBatch")
    # The device budget K for cloud-batch fan-out dispatched from this target (BE-0336 Unit 4). None =
    # no cap from this target's config (the fan-out is unbounded by config; a per-request
    # `deviceBudget` may still lower it). When set, serve keys the job registry's concurrency cap on
    # the batch *provider* (the shared Device Farm device pool), so at most K runs reserve devices on
    # that provider at once — across all targets and orgs sharing it. A target with `cloudBatch:
    # devicefarm` and K=2 means "dispatch a fan-out only when fewer than 2 devicefarm runs are
    # in-flight total, not just for this target." This is intentional: the cap bounds the real device
    # quota, not each target's slice of it. Must be a positive integer — a non-positive literal is
    # rejected at config load rather than silently meaning "unlimited" (determinism first).
    cloud_batch_budget: int | None = Field(default=None, alias="cloudBatchBudget", gt=0)
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
    # The former `alertHandling` / `dismissAlerts` spellings were deleted with no alias (BE-0401);
    # the validator below names the replacement.
    system_alert_handling: SystemAlertHandlingField = Field(
        default=None,
        validation_alias=AliasChoices("systemAlertHandling"),
        serialization_alias="systemAlertHandling",
    )
    erase: bool | None = None  # default for preconditions.erase (built-in: off)
    # Default for a scenario's iosTipKitHandling (built-in: off — a tip is sometimes the assertion).
    ios_tip_kit_handling: bool | None = Field(default=None, alias="iosTipKitHandling")
    network: bool | None = None  # collect the app's network exchanges (built-in: on)
    # App-wide interstitial-screen handlers (BE-0314): the same `{ condition, steps }` shape a
    # scenario's `interrupts` uses, applied to every scenario for this target. A scenario's own
    # `interrupts` is appended after these (config entries checked first), mirroring how
    # `systemAlertHandling` layers a config default under a per-scenario value. Empty = no app-wide
    # handler.
    interrupts: list[Interrupt] = Field(default_factory=list)
    # App-wide setup and teardown phases (BE-0392): the same `before` / `after` shapes a scenario
    # takes, applied to every scenario for this target. `before` runs *ahead* of a scenario's own
    # (config-then-scenario, like `interrupts`); `after` runs *behind* it (scenario-then-config), so
    # a scenario releases what it created before the app-wide teardown closes around it. Both empty
    # = no app-wide phase. `before` does not replace `setup` above: only `before` is its own report
    # phase, and it runs ahead of the prelude `setup` splices onto `steps`.
    before: list[Step] = Field(default_factory=list)
    after: list[AfterRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_component_in_target_steps(self) -> Self:
        # `use` and `group` are both expanded when a *scenario* file loads, which a target config
        # never passes through: an app-wide `use` or `group` would reach the step loop with no
        # action on it and abort the whole run with an `AssertionError` rather than fail one
        # scenario. Reject both here, loudly and at load time, until config-level component
        # resolution exists. Every field that takes the step grammar is covered, not only the
        # lifecycle phases (BE-0392): `interrupts` (BE-0314) skips the same expansion pass for the
        # same reason.
        field_steps = {
            "before / after": [*self.before, *(s for rule in self.after for s in rule.steps)],
            "interrupts": [s for entry in self.interrupts for s in entry.steps],
        }
        for field, steps in field_steps.items():
            if any(s.use is not None for s in steps):
                raise ValueError(
                    f"targets.<name>.{field} cannot use a component (`use`): components are "
                    "expanded per scenario file, so an app-wide one is never resolved"
                )
            if any(s.group is not None for s in steps):
                raise ValueError(
                    f"targets.<name>.{field} cannot use a group (`group`): groups are "
                    "expanded per scenario file, so an app-wide one is never resolved"
                )
        return self

    @model_validator(mode="before")
    @classmethod
    def _reject_renamed_alert_keys(cls, data: Any) -> Any:
        # `systemAlertHandling` renamed `alertHandling`, which had itself renamed `dismissAlerts`
        # (BE-0317 / BE-0327). Both aliases were deleted rather than carried a third time (BE-0401),
        # so name the canonical key here instead of leaving Pydantic's generic extra-field error.
        for old in ("alertHandling", "dismissAlerts"):
            reject_renamed_key(data, surface="config", old=old, new="systemAlertHandling")
        return data

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
