"""Human-in-the-loop handoff during record — offline, no API key, no browser, no human (BE-0179).

`record` can pause when the AI hits something it cannot supply — a one-time code, a CAPTCHA — hand
control to a human, take their response, and resume by re-observing the live screen. The human stays
at authoring time; the recorded scenario still replays with no human on the deterministic `run` path.

The real thing is `make -C demos/web record-handoff` (real Claude + a headed browser you operate
when it pauses). To make the *mechanism* reproducible without a model, a browser, or a live human,
this demo injects deterministic stand-ins — a scripted agent that raises the "needs human" turn on
the verification screen, and a scripted handoff responder standing in for the person — around the
*real* record loop and the *real* handoff contract. Only the model, the backend, and the human are
stubbed; the pause/resume is the shipped code.

    make -C demos/web record-handoff-offline
    uv run python demos/web/record/record_handoff_offline.py

The mock app mirrors the verification flow in demos/web/app/index.html: a "Verify a device" button
opens a screen with a one-time code the AI cannot know. The scripted agent opens it, then hands off;
the scripted responder answers "I operated the device" (as a human would after typing the code in
the real browser); the mock advances to the verified screen and the loop finishes.
"""

from __future__ import annotations

from bajutsu.agent import Observation, Proposal
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.handoff import HandoffRequest, HandoffResponse
from bajutsu.record import record
from bajutsu.scenario import Assertion, Step, dump_scenarios


def _el(identifier: str, label: str, traits: list[str]) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits,
        "value": None,
        "frame": (0.0, 0.0, 320.0, 44.0),
    }


def _onboarding() -> list[base.Element]:
    return [_el("verify.start", "Verify a device", ["button"])]


def _verify() -> list[base.Element]:
    # The one-time code is display-only text with no addressable id — the AI has nothing to derive
    # the value from, exactly as an out-of-band code would be. The human reads it and acts.
    return [
        _el("verify.title", "Device verification", ["staticText"]),
        _el("verify.input", "One-time code", ["textField"]),
        _el("verify.submit", "Verify", ["button"]),
    ]


def _verified() -> list[base.Element]:
    return [_el("verified.title", "Device verified ✓", ["staticText"])]


def make_app(state: dict[str, bool]) -> FakeDriver:
    """A mock of the demo's verification flow: onboarding -> verify -> (human acts) -> verified.

    `verify.submit` only advances once the human "acted" (shared state), mirroring the real app
    where submitting a wrong/empty code stays on the verification screen.
    """

    def react(driver: FakeDriver, kind: str, arg: object) -> None:
        if kind != "tap" or not isinstance(arg, dict):
            return
        target = arg.get("id")
        if target == "verify.start":
            driver.screen = _verify()
        elif target == "verify.submit" and state["acted"]:
            driver.screen = _verified()

    return FakeDriver(screen=_onboarding(), react=react)


class VerifyAgent:
    """A scripted stand-in for ClaudeAgent: open verification, hand off on the code, then finish.

    Turn by turn (indexed by how many steps were recorded): tap "Verify a device", then — on the
    verification screen — raise the "needs human" turn (the shipped `ask_human` outcome the real
    agent emits for an unknowable code), then tap Verify, then finish once verified.
    """

    def __init__(self) -> None:
        self._asked = False

    def plan(self, _goal: str) -> list[str]:
        return []

    def next_action(self, obs: Observation) -> Proposal:
        ids = {e["identifier"] for e in obs.screen}
        if "verify.start" in ids:
            return Proposal(step=Step.model_validate({"tap": {"id": "verify.start"}}))
        if "verified.title" in ids:
            return Proposal(
                done=True,
                expect=[Assertion.model_validate({"exists": {"id": "verified.title"}})],
            )
        if not self._asked:  # on the verification screen: the AI cannot know the out-of-band code
            self._asked = True
            return Proposal(
                needs_human=True,
                human_prompt="enter the one-time verification code shown on the device",
            )
        return Proposal(step=Step.model_validate({"tap": {"id": "verify.submit"}}))


class ScriptedHuman:
    """A stand-in for the person: acts on the device (types the code, submits) and resumes."""

    def __init__(self, app_state: dict[str, bool]) -> None:
        self._state = app_state

    def request(self, request: HandoffRequest) -> HandoffResponse:
        print("\n  ✋ record paused — the AI cannot know the out-of-band one-time code.")
        print(f"     it asks: {request.reason}")
        # A real human would type the code into the visible browser and submit; here the stand-in
        # marks the mock as operated and answers "I acted on the device — re-observe".
        self._state["acted"] = True
        print("  ⌨  (human enters the code in the browser and submits, then resumes)\n")
        return HandoffResponse(acted=True)


def main() -> int:
    state = {"acted": False}  # shared by the mock app and the scripted human
    print("Natural-language goal:\n  Verify a device: complete the one-time code step\n")
    # No `report=` here: the ScriptedHuman narrates the pause, and streaming the loop's per-turn
    # narration through bare `print` isn't needed for the demo (record_offline.py omits it too).
    scenario = record(
        make_app(state),
        "verify a device with the one-time code",
        VerifyAgent(),
        name="device verification (human-in-the-loop)",
        with_screenshot=False,
        handoff=ScriptedHuman(state),
    )
    print("\nRecorded scenario (plain YAML, no AI, no human on the run path):\n")
    print(dump_scenarios([scenario]))
    print(
        "In production this same loop uses ClaudeAgent + a headed Playwright browser you operate — "
        "`make -C demos/web record-handoff`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
