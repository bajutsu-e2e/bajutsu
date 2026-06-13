"""Authoring agent abstraction (Tier 1).

The agent proposes the next action from an observation. The record loop executes
proposals to advance the app and writes out a deterministic scenario. Keeping the
agent behind a protocol lets the loop be tested with a scripted fake; the Claude
implementation lives in claude_agent.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from bajutsu.drivers import base
from bajutsu.scenario import Assertion, Step


@dataclass
class Observation:
    goal: str
    screen: list[base.Element]
    history: list[Step]
    screenshot: bytes | None = None  # PNG bytes of the current screen, for vision
    plan: list[str] = field(default_factory=list)  # the goal decomposed into ordered concrete steps


@dataclass
class Proposal:
    """The agent's next move: an action to take, or done (with the goal's checks)."""

    step: Step | None = None
    done: bool = False
    expect: list[Assertion] = field(default_factory=list)
    note: str = ""


class Agent(Protocol):
    def next_action(self, observation: Observation) -> Proposal: ...

    def plan(self, goal: str) -> list[str]:
        """Decompose `goal` into an ordered list of concrete, human-readable steps.

        Called once before the record loop starts so the procedure can be explained to
        the watcher and fed back to the agent each turn (via `Observation.plan`). Optional:
        the loop treats a missing `plan` (or one that returns []) as "no up-front plan".
        """
        ...
