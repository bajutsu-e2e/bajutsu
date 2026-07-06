"""Candidate flow scenarios from a crawl (BE-0038).

Each screen the crawl discovers carries the replayable action path that reached it (`ScreenMap.paths`).
This turns those paths into draft `Scenario` files — one per discovered flow — that a user can review
and promote into a real Tier-2 test. Like crash repros, it is a pure, deterministic, model-free
function of the `ScreenMap`: no device, no LLM, never a verdict. The conversion is the shared
`crawl_repro.scenario_from_actions`, so a flow reproduces exactly what the crawl walked.

The output is a *proposal* for human review (DESIGN §6.5), never silently written into committed
scenarios. A path that can't be faithfully replayed (it taps a normalized coordinate) or is empty
(the entry screen itself) yields no scenario rather than a lossy one.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.crawl import ScreenMap
from bajutsu.crawl_repro import scenario_from_actions
from bajutsu.scenario.serialize import dump_scenario_file


def write_flows(out_dir: Path, screen_map: ScreenMap) -> list[Path]:
    """Write one candidate flow scenario per faithfully reachable discovered screen.

    Files land under `out_dir/flows/flow-NNN.yaml`, numbered sequentially (1-based) over the flows
    actually written — screens ordered by path length then fingerprint, so the shortest flows come
    first and the ordering is deterministic. The entry screen (empty path) and any screen reached via
    an unreplayable path are skipped. Returns the paths written.
    """
    flows_dir = out_dir / "flows"
    written: list[Path] = []
    ordered = sorted(screen_map.paths.items(), key=lambda item: (len(item[1]), item[0]))
    for fp, actions in ordered:
        name = f"flow-{len(written) + 1:03d}"
        scenario = scenario_from_actions(actions, name=name)
        if scenario is None:
            continue
        flows_dir.mkdir(parents=True, exist_ok=True)
        path = flows_dir / f"{name}.yaml"
        path.write_text(
            dump_scenario_file(
                [scenario],
                description=f"Candidate flow from crawl: reaches screen {fp[:7]} in "
                f"{len(scenario.steps)} step(s)",
            ),
            encoding="utf-8",
        )
        written.append(path)
    return written
