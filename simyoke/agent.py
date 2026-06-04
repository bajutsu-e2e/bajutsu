"""Authoring agent abstraction (Tier 1).

The agent proposes the next action from an observation. The record loop executes
proposals to advance the app and writes out a deterministic scenario. Keeping the
agent behind a protocol lets the loop be tested with a scripted fake; the Claude
implementation lives in claude_agent.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from simyoke.drivers import base
from simyoke.scenario import Assertion, Step


@dataclass
class Observation:
    goal: str
    screen: list[base.Element]
    history: list[Step]


@dataclass
class Proposal:
    """The agent's next move: an action to take, or done (with the goal's checks)."""

    step: Step | None = None
    done: bool = False
    expect: list[Assertion] = field(default_factory=list)
    note: str = ""


class Agent(Protocol):
    def next_action(self, observation: Observation) -> Proposal: ...
