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
from dataclasses import dataclass, field

from bajutsu.common.backends import ensure_web_runtime
from bajutsu.common.config import Effective
from bajutsu.common.config.accessors import web_engine
from bajutsu.common.config.preconditions import effective_conditions, remote_condition_problems
from bajutsu.common.devices import facts
from bajutsu.common.devices.facts import DeviceFacts
from bajutsu.common.devices.runs import Combination, Participant, combine, form_target_runs
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
    from it takes its own lanes. `engines` names the engine each web target is driven with.
    """

    devices: dict[str, tuple[str, ...]]
    engines: dict[str, str]
    runs: list[PlannedRun] = field(default_factory=list)


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
        return facts.read_ios(lane.udids)
    if platform == "android":
        return facts.read_android(lane.udids)
    raise facts.DeviceReadError(
        f"target {lane.name!r}: platform {platform} has no device to match runsOn against"
    )


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
            else effective_conditions(lanes[name].eff, s.preconditions, engine_flag=engine_flag)
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
    serves) fails on any condition, from the target or the scenario; the check looks at each target
    alone, so a local target beside a grid target is matched as usual. The members of a device group
    share one device, so their effective conditions must agree.
    """
    problems: list[str] = []
    for s in scenarios:
        for group in participants_of(s, primary):
            merged = [
                effective_conditions(effs[name], s.preconditions, engine_flag=engine_flag)
                for name in group
            ]
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
            scenario's own.
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
                devices_by_target, engines = _placement(participants, combo)
                key = (tuple(sorted(devices_by_target.items())), tuple(sorted(engines.items())))
                cells.setdefault(key, Cell(devices_by_target, engines)).runs.append(record)
    return DeviceMatrix(cells=list(cells.values()), records=records)


def _browsers(
    lane: TargetLanes,
    conditions: Mapping[str, str | list[str]],
    cache: dict[str, DeviceFacts],
    read_browser: Callable[[str], DeviceFacts],
) -> list[DeviceFacts]:
    """The launched browser of each engine a `browser.version` range compares, read once each."""
    if "browser.version" not in conditions:
        return []
    listed = conditions.get("browser.engine") or web_engine(lane.eff)
    engines = listed if isinstance(listed, list) else [listed]
    for engine in engines:
        if engine not in cache:
            cache[engine] = read_browser(engine)
    return [cache[engine] for engine in engines]


def _placement(
    participants: Sequence[Participant], combo: Combination
) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """The devices and engines every target of a runnable combination takes."""
    devices: dict[str, tuple[str, ...]] = {}
    engines: dict[str, str] = {}
    for p, run in zip(participants, combo.runs, strict=True):
        for name in p.targets:
            if p.targets[0] in combo.devices:
                devices[name] = combo.devices[p.targets[0]]
            if run.engine is not None:
                engines[name] = run.engine
    return devices, engines


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
