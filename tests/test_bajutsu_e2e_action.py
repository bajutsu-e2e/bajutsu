"""The `bajutsu-e2e` composite action's `log-predicate` input, which narrows a lane's `deviceLog`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]


def _action() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(
        (_ROOT / ".github/actions/bajutsu-e2e/action.yml").read_text()
    )
    return loaded


def test_the_predicate_defaults_to_empty_so_other_lanes_stream_unchanged() -> None:
    # `run` reads an empty `--log-predicate` as none (`plan.log_predicate or None`).
    assert _action()["inputs"]["log-predicate"]["default"] == ""


def test_the_predicate_reaches_run_as_one_quoted_argument() -> None:
    step = next(s for s in _action()["runs"]["steps"] if s.get("id") == "run")
    assert step["env"]["LOG_PREDICATE"] == "${{ inputs.log-predicate }}"
    assert '--log-predicate "$LOG_PREDICATE"' in step["run"]


def test_the_actuation_lane_keeps_the_app_and_the_system_processes_it_needs() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/ios-e2e.yml").read_text())
    step = next(
        s
        for s in workflow["jobs"]["actuation"]["steps"]
        if s.get("uses") == "./.github/actions/bajutsu-e2e"
    )
    predicate = step["with"]["log-predicate"]
    for process in ("BajutsuShowcaseSwiftUI", "SpringBoard", "runningboardd"):
        assert f'"{process}"' in predicate
