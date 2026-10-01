"""The resolved config for one target — what every run path reads instead of the raw YAML."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from bajutsu.common.config.schema import (
    DeviceProvider,
    LaunchServer,
    Mailbox,
    MockServer,
    NotifyEndpoint,
    XcuitestConfig,
)
from bajutsu.common.drivers import base
from bajutsu.common.scenario import Redact

from .ai_config import AiConfig
from .android_config import AndroidConfig
from .doctor_thresholds import DoctorThresholds
from .evidence_dirs import EvidenceDirs
from .ios_config import IosConfig
from .run_defaults import RunDefaults
from .web_config import WebConfig

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
    # Where this target's devices come from (BE-0236). None = the built-in local provider (today's
    # locally-attached `--udid` path); a device-cloud `kind` reserves a device off-host. Resolved by
    # `acquire_device` against the provider registry — off the deterministic verdict path.
    device_provider: DeviceProvider | None = None
    # Which registered batch provider serve's per-scenario fan-out submits this target's cloud runs
    # to (BE-0336 Unit 3). None = local devices; a value (e.g. "devicefarm") names a registry kind.
    cloud_batch: str | None = None
    # The device budget K for cloud-batch fan-out dispatched from this target (BE-0336 Unit 4). None =
    # no cap from config. When set, serve keys the job registry's concurrency cap on the batch
    # *provider* (the shared Device Farm device pool) — the count spans all targets and orgs sharing
    # the same provider, so K caps total in-flight device reservations, not just this target's slice.
    cloud_batch_budget: int | None = None
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

    @property
    def app_identifier(self) -> str | None:
        """The bundle identifier or package the app installs under, or None for a web target.

        Two targets sharing one are two builds of one app: installing either replaces the other on
        a device they share (BE-0447).
        """
        if isinstance(self.platform_config, IosConfig):
            return self.platform_config.bundle_id or None
        if isinstance(self.platform_config, WebConfig):
            return None
        return self.platform_config.package or None

    def rebased(
        self, root: Path, *, confine: bool = True, confine_to: Path | None = None
    ) -> Effective:
        """A copy with the relative path fields resolved against `root`.

        The common path fields — `scenarios` / `baselines` / `schemas` / `goldens` — and the iOS or
        Android sub-config's `app_path` are rebased; a future path field is rebased by adding it here. `build`
        (a shell command) and `setup` (resolved relative to the scenario, not the cwd) are
        intentionally absent. Called for a Git checkout (BE-0063), for an uploaded bundle, and — `root`
        the config file's own directory — for a local config (BE-0242), so the caller's working
        directory no longer decides where a config's paths point.

        `confine` gates the escape check: when true, an absolute or `../` value that would leave the
        confinement boundary raises ValueError, mirroring the serve-hardening path confinement
        (BE-0051). That boundary is `root` itself by default — the same directory relative paths
        resolve against — which is right for both a Git checkout and an uploaded bundle, though `root`
        means a different tree to each. For a Git checkout `root` is the whole fetched tree and the
        config may sit anywhere within it, so a sibling reference elsewhere in the checkout is
        legitimate and only a path leaving the tree is refused. For an uploaded bundle `root` is
        instead the config file's own directory (`config_path.parent` — the bundle root, or the single
        wrapper subdirectory `find_bundle_config` accepts), so the config always sits at its top and a
        `../` reference to a sibling outside that directory is refused, not legitimate.
        `confine_to` overrides just the confinement boundary, decoupling it from the resolution base,
        for serve's local file-browser bind (`bind_config`): paths there still resolve against the
        config file's own directory (`root`, matching `state.binding.cwd`), but are confined to `--root`
        (`confine_to`) — the file-browser's own sandbox, not the narrower single directory the config
        happens to live in — so a config at `<root>/configs/x.yaml` may still reference a sibling tree
        like `<root>/scenarios` via `../scenarios`, exactly as the CLI's unconfined `--config` would
        resolve it, while a path leaving `--root` entirely is still refused.

        A fetched Git config and an uploaded bundle always pass `confine=True` (boundary = `root`) —
        either can carry attacker-authored path fields (a Git spec names any repo, an upload is any
        zip), so their paths are confined regardless of who requested the bind. The two local-config
        entry points diverge on this axis only: the CLI's `--config` (an operator typing a path at
        their own terminal, BE-0121) passes `confine=False` and may point anywhere, while serve's
        `bind_config` passes `confine=True, confine_to=state.root` as a defense-in-depth consistency
        check — not because the file it binds is less trusted (`bind_config` only ever resolves a file
        `_confined_config_path` already found inside `--root`, an operator-controlled tree, so its
        content is no less trusted than the CLI's), but so a config's path *fields* can't point outside
        that same already-sanctioned tree, regardless of bind source. This is independent of `build:`
        trust (`bajutsu.serve.operations.dispatch._governed_build`): `bind_config` keeps a local
        config's build trusted exactly as the CLI path does, since neither can hand the host an
        attacker-authored command the way an API-bound Git spec or upload can.
        """
        root_resolved = root.resolve()
        confine_resolved = confine_to.resolve() if confine_to is not None else root_resolved

        def at(field: str, value: str | None) -> str | None:
            if not value:
                return value
            candidate = root / value
            if confine and not candidate.resolve().is_relative_to(confine_resolved):
                raise ValueError(f"config field {field!r} escapes the confinement root: {value!r}")
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
