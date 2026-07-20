"""Authoring agent abstraction (Tier 1).

The agent proposes the next action from an observation. The record loop executes
proposals to advance the app and writes out a deterministic scenario. Keeping the
agent behind a protocol lets the loop be tested with a scripted fake; the Claude
implementation lives in agents/claude.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from bajutsu.drivers import base
from bajutsu.scenario import Assertion, Scenario, Selector, Step

# How a human-supplied value (BE-0182) is resolved deterministically at run time: a `totp` / `email`
# step (BE-0046) that produces a `${vars.*}`, or a declared `${secrets.*}`. The agent *proposes* one
# (authoring, not judging); the author confirms and wires it.
HumanValueClass = Literal["totp", "email", "secret"]


@dataclass
class Observation:
    """What the agent sees one turn: the goal, the current screen, and history so far."""

    goal: str
    screen: list[base.Element]
    history: list[Step]
    screenshot: bytes | None = None  # PNG bytes of the current screen, for vision
    plan: list[str] = field(default_factory=list)  # the goal decomposed into ordered concrete steps
    # Whether this session can ever supply a screenshot (BE-0192). False in `--no-screenshot` mode,
    # where vision is off for every turn — so the agent must not be nudged to escalate for one it
    # can never get. When True, a `screenshot is None` turn is merely on-demand-skipped, not blind.
    vision_available: bool = True


@dataclass
class Proposal:
    """The agent's next move: an ordered batch of actions, done (with the goal's checks), or needs human.

    `steps` is the ordered actions the agent judges executable from the *current* observation
    without seeing the result of the earlier ones (BE-0178) — a single action is the length-1
    case. The record loop executes them in order and aborts the moment the screen changes out
    from under the plan, so a batch never acts on a stale screen.
    """

    steps: list[Step] = field(default_factory=list)
    done: bool = False
    expect: list[Assertion] = field(default_factory=list)
    note: str = ""
    # The 1-based plan step this move carries out (from `Observation.plan`), for live progress;
    # None when the agent gave no plan or did not attribute the move to one.
    plan_step: int | None = None
    # A third turn outcome (BE-0179), distinct from `done` and a no-action stop: the agent cannot
    # proceed and needs a human — to supply a value it cannot know or perform an operation it
    # cannot. `human_prompt` says why (shown to the human); the record loop turns this into a
    # handoff request. The heuristics that *set* it (an OTP-looking field, a repeatedly
    # unresolvable target) belong to the child items; this is only the outcome they raise.
    needs_human: bool = False
    human_prompt: str = ""
    # The value-handoff specialization of `needs_human` (BE-0182): the agent flags the *field* it
    # cannot fill (an OTP / verification code it can locate but not know) so the loop can type the
    # human's value into it live and record a deterministic placeholder step. `human_field` is the
    # target field; `human_classify` is the source the agent *proposes* for the run-time bridge — a
    # totp / email step (BE-0046) that produces a `${vars.*}`, or a declared `${secrets.*}` — which
    # the author confirms and wires (the AI proposes, never judges); `human_var` is the suggested
    # placeholder name. All None on a bare (fieldless) handoff or the takeover pattern.
    human_field: Selector | None = None
    human_classify: HumanValueClass | None = None
    human_var: str | None = None
    # A fourth turn outcome (BE-0192): on a text-only turn (no screenshot attached) the agent
    # cannot proceed from the element list alone and asks to see the screen. The record loop
    # re-issues the same observation once with the screenshot attached, rather than acting blind.
    # This is an authoring-time request, never on the `run` path.
    need_screenshot: bool = False

    @property
    def step(self) -> Step | None:
        """The first proposed step, or None — a convenience for single-action callers."""
        return self.steps[0] if self.steps else None


class Agent(Protocol):
    """The authoring agent: proposes the next action from an observation."""

    def next_action(self, observation: Observation) -> Proposal: ...

    def plan(self, goal: str) -> list[str]:
        """Decompose `goal` into an ordered list of concrete, human-readable steps.

        Called once before the record loop starts so the procedure can be explained to
        the watcher and fed back to the agent each turn (via `Observation.plan`). Optional:
        the loop treats a missing `plan` (or one that returns []) as "no up-front plan".
        """
        ...


# ---------------------------------------------------------------------------
# Enrichment (BE-0014)
# ---------------------------------------------------------------------------


@dataclass
class StepContext:
    """What the enrichment agent sees for one replayed step: the step and the screen after it."""

    step: Step
    screen: list[base.Element]
    screenshot: bytes | None = None


@dataclass
class EnrichmentProposal:
    """The agent's proposed assertions for an existing scenario."""

    expect: list[Assertion] = field(default_factory=list)
    settle: Step | None = None
    note: str = ""


class EnrichmentAgent(Protocol):
    """Proposes assertions for a scenario whose steps have already been replayed."""

    def propose_assertions(
        self,
        scenario: Scenario,
        step_contexts: list[StepContext],
    ) -> EnrichmentProposal: ...
