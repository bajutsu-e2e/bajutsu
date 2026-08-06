"""Deterministic checks on the advisory review workflow's trigger and event reach (BE-0347).

BE-0347 narrows `.github/workflows/claude-review.yml` from reviewing on every push to reviewing on
open/reopen plus an explicit `@claude review` request, so that a fix-push-review cycle cannot run
unbounded. Everything else the item changes is procedure prose no test can judge; what *is*
machine-checkable is the workflow's own wiring, and each assertion below pins one way the narrowing
silently breaks:

- `synchronize` creeping back into the trigger would restore the per-push cycle;
- the prior-findings step or the review prompt staying gated to `pull_request` would leave the
  on-demand run — now the common path — without dedup or the review contract;
- `prose-companion` staying gated to `pull_request` would strand every wording finding raised after
  the open event, quietly disabling BE-0343 for the rest of the PR's life.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "claude-review.yml"


def _workflow() -> dict[str, Any]:
    parsed = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _triggers(doc: dict[str, Any]) -> dict[str, Any]:
    """The `on:` block, which YAML resolves to the boolean ``True`` rather than the string "on"."""
    return doc[True] if True in doc else doc["on"]


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def test_the_automatic_trigger_excludes_pushes() -> None:
    """A push to the PR branch must not start a review — only opening or reopening it does."""
    types = _triggers(_workflow())["pull_request"]["types"]
    assert sorted(types) == ["opened", "reopened"]


def test_the_on_demand_comment_events_still_trigger() -> None:
    """The `@claude review` path is what replaces the per-push runs, so both comment events stay."""
    triggers = _triggers(_workflow())
    assert triggers["issue_comment"]["types"] == ["created"]
    assert triggers["pull_request_review_comment"]["types"] == ["created"]


def test_the_prior_findings_step_runs_on_every_event() -> None:
    """An on-demand run without the prior findings would re-post findings already settled."""
    step = _step(_workflow()["jobs"]["review"], "Compute the review inputs (prior findings)")
    assert "github.event_name" not in step["if"]


def test_the_review_prompt_is_supplied_on_every_event() -> None:
    """An empty prompt falls through to the action's default mention handling, which would run the
    on-demand pass without the repository's contract, its severity floor, or its dedup input."""
    step = _step(_workflow()["jobs"]["review"], "Run the advisory Claude review")
    prompt = step["with"]["prompt"]
    assert "github.event_name" not in prompt
    assert ".github/claude-review-prompt.md" in prompt


def test_the_prose_companion_job_reaches_the_comment_events() -> None:
    """BE-0343 must keep working past the open event, where every later review now arrives."""
    condition = _workflow()["jobs"]["prose-companion"]["if"]
    assert "issue_comment" in condition
    assert "pull_request_review_comment" in condition
    # The same trusted-actor gate the review job applies: a comment that cannot start a review must
    # not start a companion PR either.
    assert "@claude review" in condition
    assert "COLLABORATOR" in condition
