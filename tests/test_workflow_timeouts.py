"""Every workflow job declares `timeout-minutes`, so none can inherit GitHub's 360-minute default.

A job that declares no bound is cancelled only after six hours, which turns a stalled runner into a
red check most of a day late — `web-e2e.yml`'s `conformance (playwright)` and `codegen (playwright)`
were cancelled at nearly four hours apiece before this invariant landed. `actionlint` cannot catch a
regression here: it validates the key once written and has no rule for a missing one, so a newly
added job would silently inherit the default again. This test is what holds the invariant instead;
the convention it pins is documented in `docs/ci.md` ("Every job declares a timeout").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _jobs(path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), path.name
    jobs = parsed.get("jobs")
    assert isinstance(jobs, dict), path.name
    return jobs


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_every_job_declares_a_timeout(workflow: Path) -> None:
    for name, job in _jobs(workflow).items():
        # GitHub rejects `timeout-minutes` on a job that calls a reusable workflow; the called
        # workflow's own job carries the bound instead.
        if "uses" in job:
            continue
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int), f"{workflow.name}:{name} declares no timeout-minutes"
        assert 0 < timeout <= 180, f"{workflow.name}:{name} timeout-minutes={timeout} out of range"


def test_the_repository_default_is_the_common_case() -> None:
    """Most jobs take the documented 30-minute default; a lane-specific value is the exception."""
    timeouts = [
        job["timeout-minutes"]
        for workflow in WORKFLOWS.glob("*.yml")
        for job in _jobs(workflow).values()
        if "uses" not in job
    ]
    assert timeouts.count(30) > len(timeouts) / 2
