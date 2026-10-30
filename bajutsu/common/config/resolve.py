"""Resolution — team defaults overlaid by per-target config, producing an `Effective`.

`resolve()` produces the effective config for one target. The loader has already merged `defaults`
into each target (`schema/_merge.py`), so resolution reads one validated target and builds today's
`Effective` from the regrouped keys (BE-0452); only `ai` merges field by field here. Scenario-level
overrides (preconditions) are applied later by the runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bajutsu.common import _yaml
from bajutsu.common.config.effective import (
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
from bajutsu.common.config.schema import (
    AiSettings,
    Config,
    TargetConfig,
    XcuitestConfig,
    _Model,
)
from bajutsu.common.config.schema.platform._shared import AppCommon
from bajutsu.common.config.schema.platform.android import AndroidApp, AndroidDriver, AndroidRunsOn
from bajutsu.common.config.schema.platform.ios import IosApp, IosDriver, IosKind, IosRun, IosRunsOn
from bajutsu.common.config.schema.platform.web import WebApp, WebDriver, WebRunsOn


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


# The built-in locale a target runs under when `runsOn.ios.locale` is unset. iOS pins the Simulator's
# own system language to it (BE-0320); the other platforms read it nowhere but carry it for the report.
DEFAULT_LOCALE = "en_US"


def _conditions(runs_on: _Model) -> dict[str, str | list[str]]:
    """The device conditions *runs_on* declares, keyed by the field that declares each.

    The keys are the fields within the destination block (`model`, `browser.version`), not their
    `runsOn` paths: they name a run's coordinates, which BE-0469 left as they were.

    A single browser or iOS kind is how the target is driven rather than a condition; a listed one
    multiplies runs, so it counts (BE-0468).
    """
    if isinstance(runs_on, IosRunsOn):
        ios = runs_on.ios
        candidates: dict[str, str | list[str] | None] = {
            "kind": list(ios.kind) if isinstance(ios.kind, list) else None,
            "model": ios.model,
            "os": ios.os,
            "family": list(ios.family) if isinstance(ios.family, list) else ios.family,
        }
    elif isinstance(runs_on, AndroidRunsOn):
        android = runs_on.android
        candidates = {"avd": android.avd, "apiLevel": android.api_level}
    elif isinstance(runs_on, WebRunsOn):
        kind = runs_on.browser.kind
        candidates = {
            "browser.kind": list(kind) if isinstance(kind, list) else None,
            "browser.version": runs_on.browser.version,
        }
    else:
        candidates = {}
    return {name: value for name, value in candidates.items() if value is not None}


def _xcuitest(driver: _Model, runs_on: _Model) -> XcuitestConfig | None:
    """Today's resolved runner block, built from `driver.runner` and `runsOn.ios.kind`, or None if unset.

    A listed kind resolves to its first; each run of the list replaces it with its own (BE-0468).
    """
    runner = driver.runner if isinstance(driver, IosDriver) else None
    kinds: list[IosKind] = runs_on.ios.kinds if isinstance(runs_on, IosRunsOn) else ["simulator"]
    kind = kinds[0]
    if runner is None or (
        runner.test_runner is None
        and runner.build is None
        and runner.host_address is None
        and "device" not in kinds
    ):
        return None
    return XcuitestConfig(
        testRunner=runner.test_runner,
        build=runner.build,
        deviceType=kind,
        hostAddress=runner.host_address,
    )


def _platform_config(a: TargetConfig) -> PlatformConfig:
    """Build the platform-specific sub-config for the target's platform (BE-0126).

    A `fake` target resolves to the iOS-shaped sub-config, as it did before BE-0452.
    """
    app = a.subject
    if isinstance(app, WebApp):
        runs_on = a.runs_on
        assert isinstance(runs_on, WebRunsOn)
        driver = a.driver
        assert isinstance(driver, WebDriver)
        kind = runs_on.browser.kind
        return WebConfig(
            base_url=app.url,
            headless=driver.headless,
            # A listed browser kind is a condition each run replaces with its own (BE-0452); the
            # first stands in so the resolved type stays one browser.
            browser=kind[0] if isinstance(kind, list) else kind,
            device_mode=runs_on.browser.emulate,
        )
    if isinstance(app, AndroidApp):
        driver = a.driver
        assert isinstance(driver, AndroidDriver)
        return AndroidConfig(
            package=app.id,
            app_path=app.path,
            build=app.build,
            grant_permissions=list(app.grant_permissions),
            native_z=driver.native_z,
        )
    if isinstance(app, IosApp):
        return IosConfig(
            bundle_id=app.id,
            app_path=app.path,
            build=app.build,
            xcuitest=_xcuitest(a.driver, a.runs_on),
        )
    return IosConfig(bundle_id=getattr(app, "id", None) or "")


def _launch(app: _Model) -> tuple[dict[str, str], list[str], str | None]:
    """The app's launch environment, arguments, and deeplink; a field the platform lacks gives none."""
    launch = getattr(app, "launch", None)
    env = getattr(launch, "env", None) or {}
    args = getattr(launch, "args", None) or []
    deeplink = getattr(launch, "deeplink", None)
    return dict(env), list(args), deeplink


def unknown_target(config: Config, target: str, *, list_targets: bool = True) -> str:
    """The error for a *target* name `targets:` lacks, which says so when it names a template.

    A template is a building block under `templates:` and never runnable (BE-0471), so a name that
    matches one gets that reason rather than a bare "unknown".

    Args:
        config: The loaded config.
        target: The name asked for.
        list_targets: Append every target the config declares. The server passes False: one
            config can hold several orgs' targets, and the list would show a caller the others'.
    """
    if target in config.templates:
        message = f"{target!r} is a template, not a target"
    else:
        message = f"unknown target: {target!r} (define targets.{target} in config)"
    if not list_targets:
        return message
    return f"{message}; targets: {', '.join(sorted(config.targets)) or 'none'}"


def resolve(config: Config, target: str) -> Effective:
    """Resolve the effective config for one target, whose `defaults` and templates the loader merged.

    Raises:
        KeyError: *target* is not under `targets:` (`unknown_target` words it).
    """
    if target not in config.targets:
        raise KeyError(unknown_target(config, target))
    a = config.targets[target]
    app = a.subject
    start_when = app.start_when if isinstance(app, AppCommon) else None
    launch_env, launch_args, deeplink = _launch(app)
    runs_on = a.runs_on
    ios = runs_on.ios if isinstance(runs_on, IosRunsOn) else None
    locale = (ios.locale if ios is not None else None) or DEFAULT_LOCALE
    seed_photos = list(ios.seed_photos) if ios is not None else []
    run = a.run
    tip_kit = run.tip_kit_handling if isinstance(run, IosRun) else None
    return Effective(
        target=target,
        platform_config=_platform_config(a),
        launch_server=a.services.server,
        start_when=start_when.exists.sel.as_selector() if start_when is not None else None,
        conditions=_conditions(runs_on),
        backend=[a.platform],
        declared_platform=a.platform,
        locale=locale,
        launch_env=launch_env,
        launch_args=launch_args,
        reinstall=getattr(app, "reinstall", None),
        deeplink=deeplink,
        seed_photos=seed_photos,
        id_namespaces=list(app.id_namespaces) if isinstance(app, AppCommon) else [],
        reserved_namespaces=list(config.reserved_namespaces),
        mock_server=a.services.mock_server,
        mailbox=a.services.mailbox,
        capture=list(a.evidence.capture),
        redact=a.evidence.redact,
        secrets=list(a.evidence.secrets),
        ai=_merge_ai(config.defaults.ai, a.ai),
        evidence_dirs=EvidenceDirs(
            scenarios=a.paths.scenarios,
            baselines=a.paths.baselines,
            schemas=a.paths.schemas,
            goldens=a.paths.goldens,
        ),
        doctor_thresholds=DoctorThresholds(
            ok_coverage=config.doctor.id_coverage_ok,
            fail_coverage=config.doctor.id_coverage_fail,
        ),
        notify=list(config.notify),
        visual_compare=a.evidence.visual_compare or "exact",
        run_defaults=RunDefaults(
            system_alert_handling=run.system_alert_handling,
            network=a.evidence.collect_network,
            ios_tip_kit_handling=tip_kit if tip_kit is not None else False,
            interrupts=list(a.hooks.interrupts),
            setup=list(a.hooks.setup),
            cleanup=list(a.hooks.cleanup),
            ignored_settings=dict(run.ignored_settings),
        ),
    )


def parse_config_dict(
    data: dict[str, Any], *, path: Path | None = None, root: Path | None = None
) -> Config:
    """Validate an already-parsed config document into a `Config`.

    Top-level `orgs:` and `ui:` keys are dropped before validation: the hosted multi-tenancy org
    model (BE-0129) and the serve UI's `ui.default_theme` (BE-0191) are `serve` concerns the
    deterministic core does not model, and a run in the hosted topology legitimately reads a config
    carrying them, so the core must ignore the keys rather than reject them under `extra="forbid"`.
    `bajutsu.serve.orgs` / `bajutsu.serve.themes` parse those blocks separately. A key starting with
    `x-` is dropped too: it only hosts a YAML anchor that targets merge with `<<` (BE-0473), and the
    loader has already expanded every alias of it. Every other unknown key still fails loudly,
    preserving the typo guard.

    Args:
        data: The parsed YAML document.
        path: The config file, which a component called from a target's `hooks` resolves against.
            None for a document with no file, whose hooks may then call no component.
        root: The suite root a component reference must stay inside (BE-0174): the checkout of a
            Git source, the bundle root of an upload or a composition. None means the config file's
            own directory.
    """
    # Only drop from an actual mapping; a non-dict document (a YAML scalar/list) flows unchanged
    # into model_validate, which raises a pydantic ValidationError (a ValueError) rather than the
    # AttributeError a `.items()` on a scalar would throw and escape the callers' handling.
    if isinstance(data, dict):
        data = {
            k: v
            for k, v in data.items()
            if k not in ("orgs", "ui") and not (isinstance(k, str) and k.startswith("x-"))
        }
    context: dict[str, Path] = {}
    if path is not None:
        context["config_path"] = path
        context["suite_root"] = root if root is not None else path.parent
    return Config.model_validate(data, context=context)


def load_config(text: str, *, path: Path | None = None, root: Path | None = None) -> Config:
    """Parse a YAML config string into a `Config` (see `parse_config_dict`)."""
    return parse_config_dict(_yaml.safe_load(text) or {}, path=path, root=root)


def load_config_file(path: Path, *, root: Path | None = None) -> Config:
    """Read and parse the config file at *path*, resolving hook components against it.

    Raises:
        OSError: The file cannot be read.
        ValueError: The document does not match the schema (a pydantic `ValidationError`) or is not
            valid YAML.
    """
    return load_config(path.read_text(encoding="utf-8"), path=path, root=root)
