"""How many devices a run's scenarios need from each pool (BE-0428).

One count for both sides of a borrowed device (BE-0511): the run, which refuses a pool too small,
and the worker, which asks a lease broker for exactly that many before the run starts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from bajutsu.common.scenario import Scenario


def pool_demand(scenarios: Sequence[Scenario], actuators: Mapping[str, str]) -> dict[str, int]:
    """The most devices any one scenario needs from each pool at once.

    Targets sharing a pool share its device queue, and a scenario holds every declared target's
    lease for its whole length, so two targets on one pool need two devices from it. A device group
    is one device however many members it holds (BE-0447), so the count is per group, keyed by the
    group's first member; a group whose members differ in backend is refused by the
    config-aware group preflight (BE-0447 unit 4).

    Args:
        scenarios: The scenarios the run attempts.
        actuators: Each target's actuator, which names the pool it draws from.
    """
    demand: dict[str, int] = {}
    for s in scenarios:
        per_pool: dict[str, int] = {}
        for group in s.device_groups:
            key = actuators[group[0]]
            per_pool[key] = per_pool.get(key, 0) + 1
        for key, n in per_pool.items():
            demand[key] = max(demand.get(key, 0), n)
    return demand
