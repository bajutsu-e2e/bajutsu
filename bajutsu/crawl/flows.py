"""Candidate flow scenarios from a crawl (BE-0038).

Each screen the crawl discovers carries the replayable action path that reached it (`ScreenMap.paths`).
This turns those paths into draft `Scenario` files — one per discovered flow — that a user can review
and promote into a real Tier-2 test. Like crash repros, it is a pure, deterministic, model-free
function of the `ScreenMap`: no device, no LLM, never a verdict. The conversion is the shared
`repro.scenario_from_actions`, so a flow reproduces exactly what the crawl walked.

The output is a *proposal* for human review (DESIGN §6.5), never silently written into committed
scenarios. A path that can't be faithfully replayed (it taps a normalized coordinate) or is empty
(the entry screen itself) yields no scenario rather than a lossy one.
"""

from __future__ import annotations

from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.crawl.core import ScreenMap
from bajutsu.crawl.repro import scenario_from_actions
from bajutsu.scenario.serialize import dump_scenario_file


def write_flows(writer: RunArtifactWriter, screen_map: ScreenMap) -> list[str]:
    """Write one candidate flow scenario per faithfully reachable discovered screen.

    Files land under `flows/flow-NNN.yaml`, numbered sequentially (1-based) over the flows
    actually written — screens ordered by path length then fingerprint, so the shortest flows come
    first and the ordering is deterministic. The entry screen (empty path) and any screen reached via
    an unreplayable path are skipped. Returns the artifact names written.

    A flow carries whatever the crawl typed, so it goes through the sink like every other run
    artifact (BE-0331) — and through the run's redactor before serialization: the login path behind
    every screen is a `path_to`, so an unredacted flow would hold the password the map beside it
    masks.
    """
    written: list[str] = []
    ordered = sorted(screen_map.paths.items(), key=lambda item: (len(item[1]), item[0]))
    for fp, actions in ordered:
        name = f"flow-{len(written) + 1:03d}"
        scenario = scenario_from_actions(actions, name, writer.redactor)
        if scenario is None:
            continue
        artifact = f"flows/{name}.yaml"
        writer.write_text(
            artifact,
            dump_scenario_file(
                [scenario],
                description=f"Candidate flow from crawl: reaches screen {fp[:7]} in "
                f"{len(scenario.steps)} step(s)",
            ),
        )
        written.append(artifact)
    return written
