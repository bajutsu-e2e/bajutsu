"""The serve dispatch reads the `systemAlertHandling` request flag.

The deprecated `alertHandling` (originally BE-0317's canonical name) and `dismissAlerts` body keys
were deleted with the schema aliases they mirrored (BE-0401), so a client sending one is rejected
with a 400 naming its replacement — the same as every other layer's removed spellings — rather than
silently dropped: `run`'s unset behaviour is per-scenario "on", so silently dropping a caller's
`{removed}: false` would arm the guard on a request that asked to disable it. `alertVisionInstruction`
is rejected on the same reasoning since BE-0402 retired the flag it rendered.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _shared import project

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.operations.dispatch import (
    _reject_run_vision_instruction,
    _run_alert_flags,
    _system_alert_handling_flag,
)


def test_system_alert_handling_canonical_key() -> None:
    assert _system_alert_handling_flag({"systemAlertHandling": True}) == (True, None)
    assert _system_alert_handling_flag({"systemAlertHandling": False}) == (False, None)


@pytest.mark.parametrize("removed", ["alertHandling", "dismissAlerts"])
def test_removed_keys_are_rejected(removed: str) -> None:
    for value in (False, True):
        result, err = _system_alert_handling_flag({removed: value})
        assert result is None
        assert err == ({"error": f"'{removed}' was removed; use 'systemAlertHandling'"}, 400)


@pytest.mark.parametrize("removed", ["alertHandling", "dismissAlerts"])
def test_removed_key_rejected_even_alongside_the_canonical_one(removed: str) -> None:
    # A removed spelling is rejected outright, never merged with or shadowed by the canonical key.
    result, err = _system_alert_handling_flag({"systemAlertHandling": True, removed: False})
    assert result is None
    assert err == ({"error": f"'{removed}' was removed; use 'systemAlertHandling'"}, 400)


_VISION_ERROR = {
    "error": "'alertVisionInstruction' is not supported by run (BE-0402); "
    "name the buttons with 'alertLabels', or write the scenario's own systemAlertHandling.rules"
}


def test_alert_vision_instruction_is_rejected() -> None:
    # BE-0402 retired the flag this key rendered. Accepting and dropping it would leave the HTTP API
    # the one entry point that inverts a caller's intent silently: "tap Allow" sent to *grant* a
    # permission would fall through to the built-in dismissive labels and deny it, which is the
    # outcome the scenario and target-config layers now exit 2 to prevent.
    assert _reject_run_vision_instruction({"alertVisionInstruction": "tap Allow"}) == (
        _VISION_ERROR,
        400,
    )
    assert _reject_run_vision_instruction({}) is None
    # And the shared flag helper stays clear of it: `start_record` / `start_crawl` call that one too,
    # and their own `--alert-vision-instruction` flag is what still carries the free-text form.
    assert _system_alert_handling_flag({"alertVisionInstruction": "tap Allow"}) == (None, None)
    # `run` reaches both through one entry point, so its own dispatch spends one check on the pair.
    assert _run_alert_flags({"alertVisionInstruction": "tap Allow"}) == (None, (_VISION_ERROR, 400))
    assert _run_alert_flags({"systemAlertHandling": False}) == (False, None)


def test_start_run_rejects_alert_vision_instruction(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    payload, code = ops.start_run(
        state,
        {"scenario": "smoke.yaml", "target": "demo", "alertVisionInstruction": "tap Allow"},
    )

    assert code == 400
    assert payload == _VISION_ERROR


def test_record_and_crawl_still_accept_alert_vision_instruction(tmp_path: Path) -> None:
    # The refusal is `run`'s alone. `record` and `crawl` keep the vision guard the key steers, so a
    # body naming it must not fail their jobs — serve simply does not surface it to them yet.
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    for payload, code in (
        ops.start_record(
            state,
            {"target": "demo", "goal": "log in", "name": "login", "alertVisionInstruction": "x"},
        ),
        ops.start_crawl(state, {"target": "demo", "alertVisionInstruction": "x"}),
    ):
        # `code != 400 or payload != _VISION_ERROR` would hold for *any* outcome but that one
        # payload, so the dispatch could start rejecting these calls for an unrelated reason and
        # the test would stay green covering nothing. Assert they are accepted outright.
        assert code != 400, payload


def test_absent_or_non_bool_is_none() -> None:
    # Tri-state: no key set (or a non-bool value) leaves the CLI/scenario default in force.
    assert _system_alert_handling_flag({}) == (None, None)
    assert _system_alert_handling_flag({"systemAlertHandling": "yes"}) == (None, None)


@pytest.mark.parametrize("removed", ["alertHandling", "dismissAlerts"])
def test_start_run_rejects_a_removed_alert_key(removed: str, tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    payload, code = ops.start_run(
        state, {"scenario": "smoke.yaml", "target": "demo", removed: False}
    )

    assert code == 400
    assert payload == {"error": f"'{removed}' was removed; use 'systemAlertHandling'"}


def test_start_record_rejects_a_removed_alert_key(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    payload, code = ops.start_record(
        state, {"target": "demo", "goal": "log in", "name": "login", "dismissAlerts": True}
    )

    assert code == 400
    assert payload == {"error": "'dismissAlerts' was removed; use 'systemAlertHandling'"}


def test_start_crawl_rejects_a_removed_alert_key(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)

    payload, code = ops.start_crawl(state, {"target": "demo", "alertHandling": False})

    assert code == 400
    assert payload == {"error": "'alertHandling' was removed; use 'systemAlertHandling'"}
