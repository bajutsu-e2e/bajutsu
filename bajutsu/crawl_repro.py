"""Deterministic crash-repro scenarios from a crawl (BE-0038).

A crash records the exact action path that collapsed the app UI. This turns that path back into a
runnable `Scenario` — a pure, deterministic, model-free function of the `ScreenMap`: no device, no
LLM, never a verdict. The emitted scenario is something `run` can replay to reproduce the crash, so
a discovered crash becomes a regression test rather than a one-off observation.

A path that taps a normalized coordinate (`tap_point`) has no selector to address, so it cannot be
faithfully replayed; such a path emits no scenario rather than a lossy one (the prime directive:
faithful or nothing).
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.crawl import Action, Crash, ScreenMap
from bajutsu.scenario.models import Scenario, Selector, Step, TypeText
from bajutsu.scenario.serialize import dump_scenario_file


def _selector(action: Action) -> Selector | None:
    """The element selector for an id- or label-addressed action, or None when it has neither.

    Id preferred; otherwise label (+ index). An action carrying no addressing condition can't be
    targeted, so it has no faithful selector — the caller treats that as an unsupported repro.
    """
    if action.target:
        return Selector(id=action.target)
    if action.label is not None:
        return Selector(label=action.label, index=action.index)
    return None


def _steps(action: Action) -> list[Step] | None:
    """Faithful step(s) for one action, or None when it has no replayable scenario form.

    A `fill` expands to one `type` step per field (mirroring how the crawl performs it); a
    `tap_point` is a coordinate the scenario schema can't address, and an action with no selector
    can't be targeted — both return None.
    """
    if action.kind == "tap":
        sel = _selector(action)
        return [Step(tap=sel)] if sel is not None else None
    if action.kind == "type":
        sel = _selector(action)
        return [Step(type=TypeText(text=action.value or "", into=sel))] if sel is not None else None
    if action.kind == "fill":
        if not action.fields or any(not fid for fid, _ in action.fields):
            return None
        return [Step(type=TypeText(text=val, into=Selector(id=fid))) for fid, val in action.fields]
    return None


def crash_scenario(crash: Crash, name: str) -> Scenario | None:
    """Build a runnable repro scenario from a crash's recorded action path.

    Returns None when the path is empty or contains an action with no faithful scenario form (a
    `tap_point`): a partial replay wouldn't reach the crash, so no scenario is better than a lossy
    one.
    """
    steps: list[Step] = []
    for action in crash.actions:
        produced = _steps(action)
        if produced is None:
            return None
        steps.extend(produced)
    if not steps:
        return None
    return Scenario(name=name, steps=steps)


def write_repros(out_dir: Path, screen_map: ScreenMap) -> list[Path]:
    """Write one repro scenario file per faithfully reproducible crash, returning the paths written.

    Files land under `out_dir/crashes/crash-NNN.yaml` (1-based, in crash order). A crash whose path
    can't be faithfully replayed is skipped, so the numbering tracks the crash list, not the files.
    """
    written: list[Path] = []
    crashes_dir = out_dir / "crashes"
    for i, crash in enumerate(screen_map.crashes, start=1):
        name = f"crash-{i:03d}"
        scenario = crash_scenario(crash, name=name)
        if scenario is None:
            continue
        crashes_dir.mkdir(parents=True, exist_ok=True)
        path = crashes_dir / f"{name}.yaml"
        path.write_text(
            dump_scenario_file([scenario], description=f"Crash repro from crawl: {name}"),
            encoding="utf-8",
        )
        written.append(path)
    return written
