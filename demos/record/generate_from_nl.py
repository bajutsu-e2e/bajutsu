"""Generate a scenario from a natural-language goal — offline, no API key.

`record` (Tier 1) is Bajutsu's authoring path: an agent reads a natural-language *goal*
plus the live screen, proposes one action at a time, and the loop writes the executed
steps out as a deterministic scenario that `run` later replays with **no AI**.

In production the agent is `bajutsu.claude_agent.ClaudeAgent` (Claude reads the goal +
a screenshot + the accessibility tree). To keep this demo reproducible without an API
key or a Simulator, we inject a deterministic stand-in — `KeywordAgent` — that parses
the *same* natural-language goal with a few keyword rules and grounds each action in the
visible elements of an in-memory FakeDriver. The record loop, the
`Observation -> Proposal` protocol, and the emitted scenario are the real ones.

    uv run python demos/record/generate_from_nl.py
    uv run python demos/record/generate_from_nl.py "tap Increment, then check the counter shows 1"
    uv run python demos/record/generate_from_nl.py --file demos/record/goals.txt

Change the goal and the generated scenario changes. The supported mini-grammar is in
this folder's README; the real, open-ended version is `bajutsu record --goal "..."`.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from bajutsu.agent import Observation, Proposal
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import RunResult, run_scenario
from bajutsu.record import record
from bajutsu.scenario import Assertion, Scenario, Step, dump_scenarios

DEFAULT_GOAL = (
    "Get started, log in with email demo@bajutsu.dev and password hunter2, "
    "then tap Increment twice, and check the counter shows 2"
)


# --- A tiny mock app: onboarding -> login -> home, scripted via FakeDriver.react ---


def _el(
    identifier: str, label: str, traits: list[str], value: str | None = None
) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits,
        "value": value,
        "frame": (0.0, 0.0, 320.0, 44.0),
    }


def _onboarding() -> list[base.Element]:
    return [_el("onboarding.start", "Get Started", ["button"])]


def _login() -> list[base.Element]:
    return [
        _el("auth.email", "Email", ["textField"]),
        _el("auth.password", "Password", ["secureTextField"]),
        _el("auth.submit", "Log In", ["button"]),
    ]


def _home(count: int) -> list[base.Element]:
    return [
        _el("home.title", "Home", ["staticText"]),
        _el("counter.value", "Counter", ["staticText"], value=str(count)),
        _el("counter.increment", "Increment", ["button"]),
    ]


def make_app() -> FakeDriver:
    """A fresh FakeDriver on the onboarding screen whose taps advance the flow."""
    state = {"count": 0}

    def react(driver: FakeDriver, kind: str, arg: object) -> None:
        if kind != "tap" or not isinstance(arg, dict):
            return
        target = arg.get("id")
        if target == "onboarding.start":
            driver.screen = _login()
        elif target == "auth.submit":
            driver.screen = _home(state["count"])
        elif target == "counter.increment":
            state["count"] += 1
            driver.screen = _home(state["count"])

    return FakeDriver(screen=_onboarding(), react=react)


# --- The deterministic stand-in for ClaudeAgent ---

_TAP_VERBS = {"tap", "press", "click", "open"}
_CHECK_VERBS = ("check", "verify", "confirm", "expect", "ensure")
_COUNTS = {"once", "twice", "thrice", "times", "time"}
_STOP = {"the", "that", "it", "a", "an"}


def _clauses(goal: str) -> list[str]:
    """Split the goal on commas / `then`, dropping a leading `and` from each clause."""
    parts = re.split(r"\s*,\s*(?:then\s+)?|\s+then\s+", goal, flags=re.IGNORECASE)
    out = []
    for part in parts:
        clause = re.sub(r"^and\s+", "", part.strip(), flags=re.IGNORECASE)
        if clause:
            out.append(clause)
    return out


def _after(word: str, clause: str) -> str:
    """The token following `word` (e.g. the value in `... shows 2`)."""
    m = re.search(rf"\b{word}\s+(\S+)", clause, flags=re.IGNORECASE)
    return m.group(1).strip(" .") if m else ""


def _repeat(clause: str) -> int:
    low = clause.lower()
    if "twice" in low:
        return 2
    if "thrice" in low:
        return 3
    m = re.search(r"(\d+)\s+times", low)
    return int(m.group(1)) if m else 1


def _tap_target(clause: str) -> str:
    """The thing to tap: the clause minus a leading verb and any count words."""
    words = clause.split()
    if words and words[0].lower() in _TAP_VERBS:
        words = words[1:]
    kept = [w for w in words if w.lower() not in _COUNTS and not w.isdigit()]
    return " ".join(kept).strip(" .") or clause


def _check_target(clause: str) -> str:
    """The thing being asserted in a `check ... shows/is X` clause."""
    body = re.sub(rf"^({'|'.join(_CHECK_VERBS)})\s+", "", clause, flags=re.IGNORECASE)
    body = re.split(r"\bshows\b|\bis\b|==", body, flags=re.IGNORECASE)[0]
    kept = [w for w in body.split() if w.lower() not in _STOP]
    return " ".join(kept).strip(" .")


# A planned step is ("tap", hint) or ("type", text, hint); an expect is (hint, value).
Plan = list[tuple[str, ...]]


def plan_from_goal(goal: str) -> tuple[Plan, list[tuple[str, str]]]:
    plan: Plan = []
    expects: list[tuple[str, str]] = []
    for clause in _clauses(goal):
        low = clause.lower()
        if "email" in low and "password" in low:  # a login clause -> type + type + submit
            plan.append(("type", _after("email", clause), "email"))
            plan.append(("type", _after("password", clause), "password"))
            plan.append(("tap", "log in"))
        elif low.startswith(_CHECK_VERBS):  # an expectation (recorded as `expect`)
            value = _after("shows", clause) or _after("is", clause)
            expects.append((_check_target(clause), value))
        else:  # a tap clause, possibly repeated
            target = _tap_target(clause)
            plan.extend(("tap", target) for _ in range(_repeat(clause)))
    return plan, expects


def _ground(
    screen: list[base.Element], hint: str, *, button: bool = False,
    field: bool = False, valued: bool = False,
) -> str:
    """The id of the visible element best matching `hint` (id substring, then label)."""
    hint = hint.lower().strip()

    def fits(e: base.Element) -> bool:
        traits = set(e["traits"])
        if button and "button" not in traits:
            return False
        if field and not ({"textField", "secureTextField"} & traits):
            return False
        return not (valued and e["value"] is None)

    cands = [e for e in screen if fits(e)]
    for match_label in (False, True):
        for e in cands:
            text = (e["label"] if match_label else e["identifier"]) or ""
            if hint and hint in text.lower():
                return e["identifier"] or ""
    raise LookupError(f"goal mentions {hint!r}, but no matching element is on screen")


class KeywordAgent:
    """A deterministic stand-in for ClaudeAgent: parse the goal once, emit one grounded
    step per turn (indexed by how many steps were already recorded), then finish."""

    def __init__(self, goal: str) -> None:
        self._plan, self._expects = plan_from_goal(goal)

    def next_action(self, obs: Observation) -> Proposal:
        i = len(obs.history)
        if i >= len(self._plan):
            expect = [
                Assertion.model_validate(
                    {"value": {"sel": {"id": _ground(obs.screen, hint, valued=True)},
                               "equals": value}}
                )
                for hint, value in self._expects
            ]
            return Proposal(done=True, expect=expect)
        kind, *rest = self._plan[i]
        if kind == "type":
            text, hint = rest
            eid = _ground(obs.screen, hint, field=True)
            return Proposal(step=Step.model_validate({"type": {"text": text, "into": {"id": eid}}}))
        eid = _ground(obs.screen, rest[0], button=True)
        return Proposal(step=Step.model_validate({"tap": {"id": eid}}))


# --- Run it: goal -> record -> scenario YAML -> deterministic replay ---


def author(goal: str) -> Scenario:
    """Drive the record loop with `goal` and return the generated scenario."""
    return record(make_app(), goal, KeywordAgent(goal), name=goal, with_screenshot=False)


def run_verbose(goal: str) -> bool:
    """Author one goal, print the full scenario YAML + replay result. Returns ok."""
    print("Natural-language goal:")
    print(f"  {goal}\n")
    try:
        scenario = author(goal)
    except LookupError as e:
        print(f"Could not author this goal: {e}")
        return False
    print(f"Recorded {len(scenario.steps)} steps. Generated scenario (plain YAML, no AI):\n")
    print(dump_scenarios([scenario]))
    driver = make_app()
    result = run_scenario(driver, scenario)
    typed = [arg for kind, arg in driver.actions if kind == "type"]
    print(f"Deterministic replay: [{'PASS' if result.ok else 'FAIL'}] {result.failure or ''}")
    print(f"  driver received types: {typed}")
    print("\nIn production this same loop uses ClaudeAgent — `bajutsu record --goal \"...\"`.")
    return result.ok


def run_compact(goal: str) -> bool:
    """Author + replay one goal, printing a single-line result. Returns ok."""
    try:
        scenario = author(goal)
        result: RunResult = run_scenario(make_app(), scenario)
    except LookupError as e:
        print(f"[ERROR] {goal}\n        {e}")
        return False
    print(f"[{'PASS' if result.ok else 'FAIL'}] {len(scenario.steps)} steps — {goal}")
    return result.ok


def _read_goals(path: Path) -> list[str]:
    """One goal per line; blank lines and `#` comments are ignored."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [s.strip() for s in lines if s.strip() and not s.lstrip().startswith("#")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a scenario from a natural-language goal (offline demo)."
    )
    parser.add_argument("goal", nargs="*", help="the goal sentence (quote it); omit for the default")
    parser.add_argument(
        "-f", "--file", type=Path,
        help="read goals from a text file (one per line; # comments allowed) and run each",
    )
    args = parser.parse_args(argv)

    if args.file is not None:
        goals = _read_goals(args.file)
        print(f"Running {len(goals)} goals from {args.file}:\n")
        results = [run_compact(g) for g in goals]
        passed = sum(results)
        print(f"\n{passed}/{len(results)} passed.")
        return 0 if passed == len(results) else 1

    goal = " ".join(args.goal).strip() or DEFAULT_GOAL
    return 0 if run_verbose(goal) else 1


if __name__ == "__main__":
    raise SystemExit(main())
