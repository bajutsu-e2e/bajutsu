"""Resolution — team defaults overlaid by per-target config, producing an `Effective`.

`resolve()` produces the effective config for one target: the target entry overrides defaults.
`backend` may be a single string or a list (normalized to a list). `redact` is merged (union).
Scenario-level overrides (preconditions) are applied later by the runner. This is the only config
submodule that depends on `bajutsu.backends` (to derive a target's platform from its backend), so
its import stays a plain top-level one confined here.
"""

from __future__ import annotations

from typing import Any

from bajutsu import _yaml
from bajutsu.backends import platform_of, resolve_actuators
from bajutsu.config.effective import (
    AiConfig,
    AndroidConfig,
    DoctorThresholds,
    Effective,
    EvidenceDirs,
    IosConfig,
    PlatformConfig,
    RunDefaults,
    WebConfig,
)
from bajutsu.config.schema import AiSettings, Config, Defaults, TargetConfig
from bajutsu.scenario import Redact


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


def _platform_config(platform: str, a: TargetConfig) -> PlatformConfig:
    """Build the platform-specific sub-config for the resolved platform (BE-0126)."""
    if platform == "web":
        return WebConfig(
            base_url=a.base_url,
            headless=a.headless,
            browser=a.browser,
            device_mode=a.device_mode,
        )
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
        platform_config=_platform_config(_effective_platform(a, d, backend), a),
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
        device_provider=a.device_provider,
        setup=a.setup,
        capture=list(d.capture),
        redact=_merge_redact(d.redact, a.redact),
        secrets=list(dict.fromkeys([*d.secrets, *a.secrets])),
        requires=list(dict.fromkeys([*d.requires, *a.requires])),
        ai=_merge_ai(d.ai, a.ai),
        evidence_dirs=EvidenceDirs(
            scenarios=a.scenarios,
            baselines=a.baselines,
            schemas=a.schemas,
            goldens=a.goldens,
        ),
        doctor_thresholds=DoctorThresholds(
            ok_coverage=d.doctor.id_coverage_ok,
            fail_coverage=d.doctor.id_coverage_fail,
        ),
        notify=a.notify if a.notify is not None else list(config.notify),
        visual_compare=a.visual_compare or d.visual_compare or "exact",
        run_defaults=RunDefaults(
            dismiss_alerts=a.dismiss_alerts,
            erase=a.erase if a.erase is not None else False,
            network=a.network if a.network is not None else True,
        ),
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
