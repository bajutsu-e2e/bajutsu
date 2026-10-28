"""A scenario's preconditions merged over one target's config, and the checks run before device work.

A scenario writes its preconditions in the target's group names (`app`, `run`), each merging over the
target's (BE-0452), and its device state under `device` (BE-0469). Where a run happens belongs to the
target alone, so no scenario field reaches the device conditions. The scenario schema cannot import
the config, so the platform-aware half lives here: `check_preconditions` validates the scenario's
fields against the platforms it drives, and `resolve_preconditions` builds what a platform
environment reads for one target.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from bajutsu.common.config.effective import Effective, IosConfig
from bajutsu.common.config.schema.platform import accepts_field, schema_for
from bajutsu.common.config.schema.target_config import run_model
from bajutsu.common.devices.runs import condition_path
from bajutsu.common.scenario import Preconditions


@dataclass(frozen=True)
class ResolvedPreconditions:
    """One target's preconditions after the scenario's merged over the target's (BE-0452).

    What a platform environment reads to install, launch, and seed the app. `launch_env` and
    `launch_args` hold the scenario's own additions, which the environment merges over the target's
    `Effective.launch_env` and appends after its `launch_args`; every other field is already merged.
    """

    erase: bool = False
    reinstall: Literal["clean", "overwrite"] = "clean"
    launch_env: dict[str, str] = field(default_factory=dict)
    launch_args: list[str] = field(default_factory=list)
    deeplink: str | None = None
    # The scenario's own locale, or None to keep the target's `Effective.locale`.
    locale: str | None = None
    seed_photos: list[str] = field(default_factory=list)

    def resolved_locale(self, target_locale: str) -> str:
        """The locale this run uses: the scenario's own, else the target's."""
        return self.locale or target_locale


def resolve_preconditions(
    eff: Effective, pre: Preconditions | None = None
) -> ResolvedPreconditions:
    """Merge a scenario's preconditions over the target *eff* resolves.

    A scenario value wins over the target's; unset falls through to the target and then to the
    built-in default (`reinstall: clean`, no erase).

    Args:
        eff: The target the scenario runs against.
        pre: The scenario's preconditions; None for a command that runs no scenario.
    """
    pre = pre or Preconditions()
    # The platform the target is driven as, not the one it declares: a `fake` target resolves to the
    # iOS-shaped sub-config and runs under the scenario's iOS locale, as the flat `locale` did.
    platform = eff.platform
    seed = pre.seed_photos_for(platform)
    locale = pre.resolved_locale("", platform)
    return ResolvedPreconditions(
        erase=pre.run.erase if pre.run.erase is not None else eff.run_defaults.erase,
        reinstall=pre.app.reinstall or eff.reinstall or "clean",
        launch_env=dict(pre.app.launch.env),
        launch_args=list(pre.app.launch.args),
        deeplink=pre.app.launch.deeplink or eff.deeplink,
        locale=locale or None,
        seed_photos=seed if seed is not None else list(eff.seed_photos),
    )


def command_preconditions(eff: Effective, *, erase: bool) -> ResolvedPreconditions:
    """The target's own preconditions for a command that runs no scenario (`record`, `crawl`, `repl`).

    The command's `--erase` decides the wipe; the target's `app.reinstall`, `app.launch.deeplink`,
    and `runsOn.ios.seedPhotos` still apply.

    Raises:
        ValueError: The target seeds photos but the command does not erase, which would seed none.
    """
    merged = replace(resolve_preconditions(eff), erase=erase)
    if merged.seed_photos and not erase:
        raise ValueError(
            f"target {eff.target!r} sets runsOn.ios.seedPhotos, which seeds only a wiped device: "
            "drop --no-erase, or clear the target's seedPhotos"
        )
    return merged


def _app_fields(pre: Preconditions) -> list[tuple[str, ...]]:
    """The `app` fields *pre* sets, as paths under `preconditions.app`."""
    launch = pre.app.launch
    candidates: list[tuple[tuple[str, ...], bool]] = [
        (("reinstall",), pre.app.reinstall is not None),
        (("launch", "env"), bool(launch.env)),
        (("launch", "args"), bool(launch.args)),
        (("launch", "deeplink"), launch.deeplink is not None),
    ]
    return [path for path, written in candidates if written]


def _run_fields(pre: Preconditions) -> list[str]:
    """The `run` fields *pre* sets that a target's `run` also has, spelled as YAML writes them."""
    run = pre.run
    candidates = [
        ("erase", run.erase is not None),
        ("systemAlertHandling", run.system_alert_handling is not None),
        ("tipKitHandling", run.tip_kit_handling is not None),
    ]
    return [name for name, written in candidates if written]


def effective_conditions(
    eff: Effective, *, engine_flag: bool = False
) -> dict[str, str | list[str]]:
    """The `runsOn` conditions one target runs under: the target's own, since BE-0469.

    A `--browser` or `--browsers` flag (*engine_flag*) names the web engine itself, so the target's
    listed engine is dropped.

    Args:
        eff: The target to run.
        engine_flag: Whether a command-line flag already names the web engine.
    """
    merged = dict(eff.conditions)
    if engine_flag:
        merged.pop("browser.engine", None)
    return merged


def remote_condition_problems(
    eff: Effective, conditions: Mapping[str, str | list[str]]
) -> list[str]:
    """Why *conditions* cannot be matched for *eff*: it has no pool to read before the run.

    A physical iPhone (`runsOn.ios.kind: device`) and a target an `appium` grid serves are never matched,
    so a condition on either fails rather than going ignored (BE-0452). `locale` and `seedPhotos` are
    device state, not conditions, so they never appear here.

    Returns:
        One message per condition, each naming the field; empty when the target can be matched.
    """
    if not conditions:
        return []
    ios = eff.platform_config if isinstance(eff.platform_config, IosConfig) else None
    if ios is not None and ios.xcuitest is not None and ios.xcuitest.device_type == "device":
        where = "a physical device (runsOn.ios.kind: device)"
    elif eff.device_provider is not None and eff.device_provider.kind != "local":
        where = f"a remote environment ({eff.device_provider.kind})"
    else:
        return []
    return [
        f"{condition_path(eff.schema_platform, name)}: target {eff.target!r} runs on {where}, which has "
        "no device pool to match a condition against; remove the condition for this target"
        for name in conditions
    ]


def check_preconditions(
    pre: Preconditions, targets: Sequence[Effective], *, erase_flag: bool | None = None
) -> list[str]:
    """Every problem with *pre* against the *targets* a scenario drives, before any device work.

    A field never goes ignored (BE-0452). An `app` field or an iOS-only `run` field that no driven
    target's platform has is a problem. After merging, a target whose `seedPhotos` is non-empty needs an effective
    `run.erase` (the flag, then the scenario, then the target), because seeding happens only on the
    erase path.

    A non-primary target with seeded photos is a problem too: the run stamps `run.erase` and seeds
    the photo library for the primary alone.

    Args:
        pre: The scenario's preconditions, as written (before a command fills in run defaults).
        targets: Every target the scenario drives, the primary first.
        erase_flag: The `--erase` / `--no-erase` flag, or None when absent.

    Returns:
        One message per problem, each naming the field; empty when the scenario is sound.
    """
    platforms = list(dict.fromkeys(eff.schema_platform for eff in targets))
    driven = ", ".join(platforms)
    unused = [
        f"app.{'.'.join(path)}"
        for path in _app_fields(pre)
        if not any(accepts_field(schema_for(p).app, path) for p in platforms)
    ] + [
        f"run.{name}"
        for name in _run_fields(pre)
        if not any(accepts_field(run_model(p), (name,)) for p in platforms)
    ]
    problems = [
        f"preconditions.{field} applies to no target this scenario drives "
        f"(platform {driven}); remove it, or name a target whose platform has it"
        for field in unused
    ]
    for i, eff in enumerate(targets):
        merged = resolve_preconditions(eff, pre)
        if i and merged.seed_photos:
            # The run resolves `run.erase` and seeds the photo library for the primary alone.
            problems.append(
                f"seedPhotos on target {eff.target!r} applies to the primary target alone; clear "
                "it for this scenario, or make the target the scenario's primary"
            )
            continue
        erase = erase_flag if erase_flag is not None else merged.erase
        if merged.seed_photos and not erase:
            problems.append(
                f"seedPhotos on target {eff.target!r} needs a wiped device: set "
                "preconditions.run.erase: true, the target's run.erase: true, or pass --erase"
            )
    return problems
