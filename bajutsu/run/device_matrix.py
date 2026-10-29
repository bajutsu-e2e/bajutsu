"""Plan a `run` invocation's runs from each scenario's effective `runsOn` conditions (BE-0452).

`bajutsu.common.devices.runs` forms one target's runs and combines them; this module applies that to
an invocation: it merges each scenario's conditions over its targets', reads the device facts of
every pool a condition needs, and groups the runs that take the same devices into *cells*. A cell
runs as one pass against pools narrowed to its eligible sets, so a run never takes a device outside
its set. Cells run one after another; `--workers` spreads a cell's runs over its identical devices.

Every step is deterministic: the pool order, the scenario order, and the declared target order
decide the result, and nothing here calls a model.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from bajutsu.common.backends import ensure_web_runtime
from bajutsu.common.config import Effective, IosConfig, WebConfig
from bajutsu.common.config.accessors import web_engine, xcuitest_targets_real_device
from bajutsu.common.config.preconditions import effective_conditions, remote_condition_problems
from bajutsu.common.devices import facts
from bajutsu.common.devices.errors import DeviceError
from bajutsu.common.devices.facts import DeviceFacts
from bajutsu.common.devices.runs import (
    Combination,
    Participant,
    combine,
    declared_runs,
    eligible_set,
    first_run,
    form_target_runs,
)
from bajutsu.common.scenario import Scenario

# The actuator the fake driver runs under; its targets match no condition.
FAKE_ACTUATOR = "fake"


@dataclass(frozen=True)
class TargetLanes:
    """One declared target as planning sees it: its config, its pool, and the devices of that pool."""

    name: str
    eff: Effective
    # The pool the target draws from: its actuator, which targets of one actuator share.
    pool: str
    udids: tuple[str, ...]


@dataclass(frozen=True)
class PlannedRun:
    """One record of the invocation: a scenario, by index, on one combination of target runs."""

    scenario: int
    combination: Combination


@dataclass
class Cell:
    """The runs that take the same devices, run as one pass.

    `devices` maps each target on a narrowed pool to the udids of its eligible set; a target absent
    from it takes its own lanes. `engines` names the browser kind each web target is driven with,
    and `kinds` the iOS kind each target that lists its kinds runs on (BE-0468).
    """

    devices: dict[str, tuple[str, ...]]
    engines: dict[str, str]
    runs: list[PlannedRun] = field(default_factory=list)
    kinds: dict[str, str] = field(default_factory=dict)


@dataclass
class DeviceMatrix:
    """Every record an invocation's conditions formed, and the cells its runnable records fall in."""

    cells: list[Cell]
    records: list[PlannedRun]

    @property
    def runnable(self) -> int:
        """How many records will execute."""
        return sum(len(cell.runs) for cell in self.cells)


def installed_browser(
    backends: list[str], read: Callable[[str], DeviceFacts] = facts.read_web
) -> Callable[[str], DeviceFacts]:
    """A browser-version reader that installs the engine first, as the web backend does on demand.

    An engine a list names may not be installed yet, and reading its version launches it.
    """

    def read_installed(engine: str) -> DeviceFacts:
        try:
            ensure_web_runtime(backends, engine)
        except RuntimeError as exc:
            raise facts.DeviceReadError(f"could not install {engine}: {exc}") from exc
        return read(engine)

    return read_installed


def read_pool_facts(lane: TargetLanes) -> list[DeviceFacts]:
    """The device facts of *lane*'s pool, read through its platform's device CLI.

    Raises:
        DeviceReadError: A device could not be read, or the platform has no device to read.
    """
    platform = lane.eff.platform
    if platform == "ios":
        return read_ios_facts(lane.eff, lane.udids)
    if platform == "android":
        return facts.read_android(lane.udids)
    raise facts.DeviceReadError(
        f"target {lane.name!r}: platform {platform} has no device to match runsOn against"
    )


def read_ios_facts(
    eff: Effective,
    udids: Sequence[str],
    read_ios: Callable[[Sequence[str]], list[DeviceFacts]] | None = None,
) -> list[DeviceFacts]:
    """The facts of an iOS pool, read through the listing each of the target's kinds needs (BE-0468).

    A Simulator target reads simctl and a physical-device target devicectl, so a udid of the other
    kind fails the read; a target that lists both kinds reads a pool that may mix them. An injected
    *read_ios* stands in for every listing.
    """
    if read_ios is not None:
        return read_ios(udids)
    if "kind" in eff.conditions:
        return facts.read_ios_pool(udids)
    if xcuitest_targets_real_device(eff):
        return facts.read_ios_device(udids)
    return facts.read_ios(udids)


def participants_of(s: Scenario, primary: str) -> list[tuple[str, ...]]:
    """The device groups *s* holds, in declared order; a scenario declaring none runs on *primary*."""
    return [tuple(g) for g in s.device_groups] or [(primary,)]


def scenario_conditions(
    s: Scenario, lanes: Mapping[str, TargetLanes], primary: str, *, engine_flag: bool
) -> dict[str, dict[str, str | list[str]]]:
    """Each target *s* drives, mapped to its effective `runsOn` conditions for *s*.

    A target the fake driver runs has no device to read, so it runs under no condition, whichever
    way it came to the fake actuator (`--backend fake`, or a `fake` target).
    """
    return {
        name: (
            {}
            if lanes[name].pool == FAKE_ACTUATOR
            else effective_conditions(lanes[name].eff, engine_flag=engine_flag)
        )
        for group in participants_of(s, primary)
        for name in group
    }


def any_conditions(
    scenarios: Sequence[Scenario],
    lanes: Mapping[str, TargetLanes],
    primary: str,
    *,
    engine_flag: bool,
) -> bool:
    """Whether any scenario runs any target under a condition, so the invocation forms runs."""
    return any(
        conditions
        for s in scenarios
        for conditions in scenario_conditions(s, lanes, primary, engine_flag=engine_flag).values()
    )


def condition_problems(
    scenarios: Sequence[Scenario],
    effs: Mapping[str, Effective],
    primary: str,
    *,
    engine_flag: bool,
) -> list[str]:
    """Every scenario whose conditions cannot be matched, named, before any device work.

    A target with no pool to read before the run (a physical iPhone, or a target an `appium` grid
    serves) fails on any condition; the check looks at each target
    alone, so a local target beside a grid target is matched as usual. The members of a device group
    share one device, so their effective conditions must agree.
    """
    problems: list[str] = []
    for s in scenarios:
        for group in participants_of(s, primary):
            merged = [effective_conditions(effs[name], engine_flag=engine_flag) for name in group]
            for name, conditions in zip(group, merged, strict=True):
                problems += [
                    f"{s.name}: {problem}"
                    for problem in remote_condition_problems(effs[name], conditions)
                ]
            if any(m != merged[0] for m in merged[1:]):
                problems.append(
                    f"{s.name}: the device group {', '.join(group)} shares one device, so its "
                    "targets' runsOn conditions must agree"
                )
    return problems


def plan(
    scenarios: Sequence[Scenario],
    lanes: Mapping[str, TargetLanes],
    primary: str,
    *,
    engine_flag: bool,
    read_facts: Callable[[TargetLanes], list[DeviceFacts]],
    read_browser: Callable[[str], DeviceFacts],
    skip: Collection[int] = (),
) -> DeviceMatrix:
    """Form every scenario's runs and group the runnable ones into cells.

    Args:
        scenarios: The runnable scenarios, in the order they report.
        lanes: Every declared target, keyed by name.
        primary: The target a scenario that declares none runs on.
        engine_flag: Whether `--browser` or `--browsers` names the web engine, which wins over the
            target's listed engines.
        read_facts: Reads the device facts of a target's pool, in pool order; called at most once
            per pool, and only for a pool a condition reads.
        read_browser: Reads the version of the browser an engine launches; called at most once per
            engine, and only for an engine a `browser.version` range compares.
        skip: Indexes of scenarios that form no run, such as those the worker capability check
            already failed; the other records keep their index into *scenarios*.
    """
    pool_facts: dict[str, list[DeviceFacts]] = {}
    browsers: dict[str, DeviceFacts] = {}
    records: list[PlannedRun] = []
    cells: dict[tuple[object, ...], Cell] = {}
    for index, s in enumerate(scenarios):
        if index in skip:
            continue
        conditions = scenario_conditions(s, lanes, primary, engine_flag=engine_flag)
        participants: list[Participant] = []
        for group in participants_of(s, primary):
            lane = lanes[group[0]]
            platform = lane.eff.platform
            devices: list[DeviceFacts]
            if platform == "web":
                devices = _browsers(lane, conditions[group[0]], browsers, read_browser)
            elif conditions[group[0]]:
                if lane.pool not in pool_facts:
                    pool_facts[lane.pool] = read_facts(lane)
                devices = pool_facts[lane.pool]
            else:
                devices = []
            runs = form_target_runs(
                group[0],
                platform,
                conditions[group[0]],
                devices,
                engine=web_engine(lane.eff) if platform == "web" else None,
            )
            participants.append(Participant(group, lane.pool, tuple(runs)))
        for combo in combine(participants, pool_facts):
            record = PlannedRun(index, combo)
            records.append(record)
            if combo.outcome == "run":
                devices_by_target, engines, kinds = _placement(participants, combo)
                key = (
                    tuple(sorted(devices_by_target.items())),
                    tuple(sorted(engines.items())),
                    tuple(sorted(kinds.items())),
                )
                cell = cells.setdefault(key, Cell(devices_by_target, engines, kinds=kinds))
                cell.runs.append(record)
    return DeviceMatrix(cells=list(cells.values()), records=records)


def _browsers(
    lane: TargetLanes,
    conditions: Mapping[str, str | list[str]],
    cache: dict[str, DeviceFacts],
    read_browser: Callable[[str], DeviceFacts],
) -> list[DeviceFacts]:
    """The launched browser of each kind a `browser.version` range compares, read once each."""
    if "browser.version" not in conditions:
        return []
    listed = conditions.get("browser.kind") or web_engine(lane.eff)
    engines = listed if isinstance(listed, list) else [listed]
    for engine in engines:
        if engine not in cache:
            cache[engine] = read_browser(engine)
    return [cache[engine] for engine in engines]


def _placement(
    participants: Sequence[Participant], combo: Combination
) -> tuple[dict[str, tuple[str, ...]], dict[str, str], dict[str, str]]:
    """The devices, browser kinds, and iOS kinds every target of a runnable combination takes."""
    devices: dict[str, tuple[str, ...]] = {}
    engines: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for p, run in zip(participants, combo.runs, strict=True):
        for name in p.targets:
            if p.targets[0] in combo.devices:
                devices[name] = combo.devices[p.targets[0]]
            if run.engine is not None:
                engines[name] = run.engine
            if run.kind is not None:
                kinds[name] = run.kind
    return devices, engines, kinds


def not_applicable_record(s: Scenario, primary: str, combo: Combination) -> dict[str, str]:
    """The manifest's record of one not-applicable run: what it was, and the condition it missed."""
    target = participants_of(s, primary)[0][0]
    return {
        "scenario": s.name,
        "target": target,
        "coordinates": combo.label,
        "reason": combo.reason,
    }


def render_not_applicable(record: Mapping[str, str]) -> str:
    """How the console lists a not-applicable record, its reason indented under its name."""
    name = f"{record['target']}/{record['scenario']}"
    if record.get("coordinates"):
        name += f" [{record['coordinates']}]"
    return f"not applicable: {name}\n  {record['reason']}"


def apply_engine(eff: Effective, engine: str) -> Effective:
    """*eff* driven with *engine*, which then stops counting as a `browser.kind` condition.

    A non-web target has no engine, so it is returned unchanged.
    """
    if not isinstance(eff.platform_config, WebConfig):
        return eff
    return replace(
        without_engine_condition(eff),
        platform_config=replace(eff.platform_config, browser=engine),
    )


def apply_kind(eff: Effective, kind: str) -> Effective:
    """*eff* run on the iOS *kind* one run of its listed kinds took, which then stops counting as a
    `kind` condition (BE-0468).

    The kind sets the XCUITest destination and every real-device narrowing, as a single `kind` does.
    A target that lists no kind is returned unchanged.
    """
    cfg = eff.platform_config
    if "kind" not in eff.conditions or not isinstance(cfg, IosConfig) or cfg.xcuitest is None:
        return eff
    conditions = {k: v for k, v in eff.conditions.items() if k != "kind"}
    xcuitest = cfg.xcuitest.model_copy(update={"device_type": kind})
    return replace(eff, conditions=conditions, platform_config=replace(cfg, xcuitest=xcuitest))


def without_engine_condition(eff: Effective) -> Effective:
    """*eff* with its listed `runsOn.browser.kind` no longer counted as a device condition.

    An engine flag names the engines itself — `--browser` one, `--browsers` the matrix that sets
    each pass's engine — so the target's own list is overridden rather than matched.
    """
    conditions = {k: v for k, v in eff.conditions.items() if k != "browser.kind"}
    return replace(eff, conditions=conditions)


def target_pool(
    eff: Effective,
    actuator: str,
    udid: str,
    *,
    engines: Sequence[str] = (),
    read_ios: Callable[[Sequence[str]], list[DeviceFacts]] | None = None,
    read_android: Callable[[Sequence[str]], list[DeviceFacts]] | None = None,
    read_browser: Callable[[str], DeviceFacts] | None = None,
) -> list[DeviceFacts]:
    """The device facts of one target's pool, as `run` reads them: each udid of *udid*, resolved.

    A web lane has no device to read; its facts are the browsers *engines* launch.

    Raises:
        DeviceError: A device could not be resolved or read.
    """
    if eff.platform == "web":
        return [(read_browser or facts.read_web)(engine) for engine in engines]
    # Imported here: the platform environments reach the device CLIs, which planning alone never needs.
    from bajutsu.common.platform_lifecycle import environment_for

    env = environment_for(actuator, udid)
    pool = [env.resolve_device(u.strip()) for u in udid.split(",") if u.strip()]
    if eff.platform == "ios":
        return read_ios_facts(eff, pool, read_ios)
    return (read_android or facts.read_android)(pool)


def first_run_device(
    eff: Effective,
    actuator: str,
    udid: str,
    *,
    read_ios: Callable[[Sequence[str]], list[DeviceFacts]] | None = None,
    read_android: Callable[[Sequence[str]], list[DeviceFacts]] | None = None,
    read_browser: Callable[[str], DeviceFacts] | None = None,
) -> tuple[Effective, str]:
    """The first run of the target's conditions, and the device it takes (BE-0452).

    A command that never multiplies runs (`record`, `crawl`, `repl`, and `doctor`'s screen probe)
    takes the first listed value and the newest major version the pool has within a range, on the
    first device of that run's eligible set. A target with no condition, and the fake actuator, keep
    *udid* as given.

    Returns:
        The configuration with the run's web engine applied, and the udid of the device it takes.

    Raises:
        ValueError: The target runs where no pool can be read, a device could not be read, or no
            available device fits; the message names the condition and the devices seen.
    """
    if not eff.conditions or actuator == FAKE_ACTUATOR:
        return eff, udid
    problems = remote_condition_problems(eff, eff.conditions)
    if problems:
        raise ValueError("\n".join(problems))
    engines: list[str] = []
    conditions = dict(eff.conditions)
    if eff.platform == "web":
        # The first run takes the first listed engine, which actuator selection already installed;
        # the rest are dropped so no run is formed for an engine never read.
        listed = conditions.get("browser.kind") or web_engine(eff)
        engine = listed[0] if isinstance(listed, list) else listed
        if "browser.kind" in conditions:
            conditions["browser.kind"] = engine
        if "browser.version" in conditions:
            engines = [engine]
    try:
        devices = target_pool(
            eff,
            actuator,
            udid,
            engines=engines,
            read_ios=read_ios,
            read_android=read_android,
            read_browser=read_browser,
        )
        run, device = first_run(
            eff.target, eff.platform, conditions, devices, engine=web_engine(eff)
        )
    except (DeviceError, LookupError) as e:
        raise ValueError(f"target '{eff.target}': {e}") from None
    if run.engine is not None:
        return apply_engine(eff, run.engine), udid
    if run.kind is not None:
        eff = apply_kind(eff, run.kind)
    return eff, device.udid if device is not None else udid


ListedOutcome = Literal["run", "failed", "not-applicable", "declared"]


@dataclass(frozen=True)
class ListedRun:
    """One run as `doctor` lists it: its coordinates, its outcome, and the devices that fit it.

    `declared` marks a run listed from the conditions alone, because no pool could be read.
    """

    label: str
    outcome: ListedOutcome
    devices: tuple[str, ...] = ()
    reason: str = ""


def list_runs(
    eff: Effective,
    conditions: Mapping[str, str | list[str]],
    devices: Sequence[DeviceFacts] | None,
) -> list[ListedRun]:
    """Each run *conditions* form on one target, with the available devices that fit it.

    The same forming and eligible sets `run` uses, so the listing never disagrees with a run. With
    *devices* None, no pool could be read, so the runs are the declared ones alone and an open range
    reads as one run per available major version.
    """
    engine = web_engine(eff) if eff.platform == "web" else None
    if devices is None:
        return [
            ListedRun(label, "declared")
            for label in declared_runs(eff.platform, conditions, engine=engine)
        ]
    listed: list[ListedRun] = []
    for run in form_target_runs(eff.target, eff.platform, conditions, devices, engine=engine):
        if run.outcome != "run":
            listed.append(ListedRun(run.label, run.outcome, reason=run.reason))
        elif eff.platform == "web":
            launched = [d.describe for d in devices if d.model == run.engine]
            listed.append(ListedRun(run.label, "run", tuple(launched) or (str(run.engine),)))
        elif (fit := eligible_set(run, devices)) is None:
            listed.append(
                ListedRun(run.label, "failed", reason="no available device fits this run")
            )
        else:
            listed.append(ListedRun(run.label, "run", tuple(d.describe for d in fit)))
    return listed
