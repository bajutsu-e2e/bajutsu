"""The serve dispatch reads the `systemAlertHandling` request flag.

The deprecated `alertHandling` (originally BE-0317's canonical name) and `dismissAlerts` body keys
were deleted with the schema aliases they mirrored (BE-0401), so a client sending one is rejected
with a 400 naming its replacement — the same as every other layer's removed spellings — rather than
silently dropped: `run`'s unset behaviour is per-scenario "on", so silently dropping a caller's
`{removed}: false` would arm the guard on a request that asked to disable it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _shared import project

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.operations.dispatch import _system_alert_handling_flag


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
