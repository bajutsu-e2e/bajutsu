"""Deterministic checks on each E2E lane's required aggregator job (BE-0305).

Every E2E lane carries one always-reporting aggregator — `E2E (iOS)` / `E2E (android)` /
`E2E (web)` — and those three job names, not the path-gated device jobs, are what `main`'s
branch-protection ruleset pins. So the aggregator is the one place on the lane where a wiring
mistake is invisible *and* consequential: it reports green, the merge goes through, and nothing
says the lane never ran.

The wiring has two halves that must agree — the jobs the aggregator `needs:`, and the results its
check step actually reads — and nothing but these tests holds them together. BE-0305 found the halves
already apart on the iOS lane: `changes` sat in `needs:` while the step read only the macOS jobs, so a
failed `changes` would skip all of them as a *dependency* failure and the gate would count that as the
path-skip pass. Android and web guarded it; iOS did not. That is a class of defect, not one typo — the
same slip lands every time a job joins `needs:` and its result is left unread — so the first test below
pins the agreement itself rather than any one job's name.

Agreement alone is too weak to rest on, because both halves can be edited together: dropping a job
from `needs:` *and* from the step reads consistently, and reinstates the same green-having-run-nothing
gap the first test was written for. So membership is pinned per lane as well — the exact job set each
gate depends on — making the removal of a gating job a test change a reviewer sees rather than a
silent edit.

Both of those are structural, and neither says the verdict script actually converts a red dependency
into a red gate. Rewriting the loop body to compare against a value no job ever reports would leave a
permanently green required check that satisfies every structural assertion, so the last test runs the
real script under `bash` and pins the outcome it produces for each result value a dependency can
carry.

Removal is not the only way in. Two further routes reach the same outcome by addition rather than by
edit: a lane added later that nothing here ever reads, and a `continue-on-error` that disarms a red —
on a gated job, where it hands `needs:` a `success` the job never earned, or on the aggregator's own
verdict step, where it reports the required check green over a script that exited non-zero. Both are
pinned as well: the lane set against the workflow directory, and that key against every gated job,
the aggregator, and the verdict step itself.

The rest pin what BE-0305 shipped: both fault-injection lanes, promoted out of per-PR-signal status
into their aggregators' `needs:` once measured stable, so a regression in the retry and crash-recovery
detection those lanes exercise for real can no longer merge as an ignorable red signal. Silently
dropping either back out would restore exactly the gap BE-0305 exists to close, and would otherwise
leave no trace.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# Every lane's aggregator carries the job id `e2e`; the display name is what the ruleset pins.
LANES = {"ios": "E2E (iOS)", "android": "E2E (android)", "web": "E2E (web)"}

# Exactly what each gate depends on. Deliberately spelled out rather than derived from the workflow:
# a derived expectation would follow the file wherever it was edited, which is the one thing this must
# not do. Widening a gate is welcome and only costs a line here; narrowing one now needs a reviewer.
# The jobs each lane deliberately leaves OFF its gate — a host-specific pixel baseline, a golden that
# upstream drift can move, a lane still proving itself — are documented at the aggregator itself.
GATED = {
    "ios": {
        "changes",
        "build",
        "run",
        "codegen",
        "bundled-runner",
        "conformance",
        "fault-injection",
    },
    "android": {"changes", "smoke", "conformance", "network", "fault-injection"},
    "web": {
        "changes",
        "web-e2e",
        "serve-ui-dogfood",
        "web-conformance",
        "web-network",
        "web-codegen",
    },
}

# What a dependency can report, and whether the gate must go red for it. A `skipped` dependency is a
# pass on purpose: the device jobs are path-gated, so a PR that cannot affect a lane skips it
# legitimately. That is exactly why `failure` must be told apart from it.
RESULTS = {"success": False, "skipped": False, "failure": True, "cancelled": True}

# `${{ needs.<job>.result }}`, the only form the aggregators use to read a dependency's outcome. The
# job id may carry hyphens (`bundled-runner`, `fault-injection`), which GitHub allows in a context path.
_NEEDS_RESULT = re.compile(r"^\$\{\{\s*needs\.([A-Za-z0-9_-]+)\.result\s*\}\}$")

# The `for result in "$A" "$B" …; do` header that decides the gate's verdict.
_LOOP = re.compile(r"for\s+result\s+in\s+(?P<vars>.+?);\s*do")


def _jobs(lane: str) -> dict[str, Any]:
    """Every job in the lane's workflow, keyed by job id."""
    parsed = yaml.safe_load((WORKFLOWS / f"{lane}-e2e.yml").read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    jobs: dict[str, Any] = parsed["jobs"]
    return jobs


def _aggregator(lane: str) -> dict[str, Any]:
    job = _jobs(lane)["e2e"]
    assert isinstance(job, dict)
    assert job["name"] == LANES[lane], (lane, job["name"])
    return job


def _check_step(job: dict[str, Any]) -> dict[str, Any]:
    """The aggregator's verdict step, selected by the loop every caller here actually needs.

    Selecting on "carries `env:` and `run:`" would instead pick up any second such step — a
    `$GITHUB_STEP_SUMMARY` line, a debug `echo` over a `needs.*` value — and redden most of this file
    at once over an addition that changed nothing about the gate.
    """
    steps = [
        step for step in job["steps"] if "env" in step and _LOOP.search(str(step.get("run", "")))
    ]
    assert len(steps) == 1, f"expected one verdict step, found {len(steps)}"
    step = steps[0]
    assert isinstance(step, dict)
    return step


def _read_jobs(step: dict[str, Any]) -> dict[str, str]:
    """The dependency each env var reads, keyed by env var name."""
    read = {}
    for name, value in step["env"].items():
        match = _NEEDS_RESULT.match(str(value))
        if match:
            read[name] = match.group(1)
    return read


def _looped_vars(step: dict[str, Any]) -> set[str]:
    """The env var names the verdict loop iterates over.

    All three spellings (`"$A"`, `$A`, `${A}`) count: a `needs.*.result` is always one bare word, so
    they behave identically here, and pinning one of them would redden the suite with a wrong
    diagnosis on a reformat that changed no wiring.
    """
    match = _LOOP.search(step["run"])
    assert match, f"no verdict loop found in:\n{step['run']}"
    return set(re.findall(r"\$\{?([A-Za-z0-9_]+)\}?", match.group("vars")))


def _disarms_its_own_failure(node: dict[str, Any]) -> bool:
    """Whether a job or a step declares its own failure not to count.

    Read as text rather than compared against `True`: importing `bajutsu` narrows PyYAML's bool
    resolver process-wide (`bajutsu/_yaml.py`), so under this suite the value arrives as the string
    `"true"` and an `is True` check would never fire — itself the never-firing assertion this file
    exists to rule out. Text holds whichever way it parses, and counts a `${{ }}` expression as
    disarming too, since nothing on the verdict path has business making its own red conditional.
    """
    return str(node.get("continue-on-error", False)).lower() != "false"


def test_every_needed_job_reaches_the_verdict() -> None:
    """A job in `needs:` whose result is never read makes the gate pass on its failure.

    A failed dependency skips the jobs downstream of it, and a skip is the aggregator's *pass* — that
    is deliberate, since a path-gated lane legitimately skips. So the only thing separating "nothing
    ran because this PR can't affect the lane" from "nothing ran because a dependency broke" is that
    every job in `needs:` has its own result inspected.
    """
    for lane in LANES:
        step = _check_step(_aggregator(lane))
        read = _read_jobs(step)
        needed = set(_aggregator(lane)["needs"])
        assert set(read.values()) == needed, (
            f"{lane}: jobs in `needs:` but never read: {sorted(needed - set(read.values()))}; "
            f"read but not needed: {sorted(set(read.values()) - needed)}"
        )
        assert set(read) == _looped_vars(step), (
            f"{lane}: env vars never checked by the verdict loop: "
            f"{sorted(set(read) - _looped_vars(step))}"
        )


def test_every_aggregator_always_reports() -> None:
    """A required check that can be skipped stays pending forever and blocks the merge instead."""
    for lane in LANES:
        assert _aggregator(lane)["if"] == "always()", lane


def test_the_fault_injection_lanes_are_on_the_gate() -> None:
    """BE-0305's promotion: a real-fault regression must block a merge, not surface as a signal.

    Both lanes landed as per-PR signals — they break the device on purpose — and were promoted once
    measured stable. Dropping either back out would leave the retry and crash-recovery mechanisms
    with no CI consequence for failing the real condition they exist to survive.
    """
    for lane in ("ios", "android"):
        assert "fault-injection" in _aggregator(lane)["needs"], lane


def test_each_gate_depends_on_the_jobs_it_is_supposed_to() -> None:
    """Dropping a job from `needs:` and from the step reads consistently, and gates nothing.

    `test_every_needed_job_reaches_the_verdict` only holds the two halves in agreement, so an edit
    that removes a job from both stays green there while quietly narrowing what the required check
    covers — including a removal of `changes`, which reinstates the original defect exactly.
    """
    for lane, expected in GATED.items():
        assert set(_aggregator(lane)["needs"]) == expected, (
            f"{lane}: no longer gates {sorted(expected - set(_aggregator(lane)['needs']))}; "
            f"newly gates {sorted(set(_aggregator(lane)['needs']) - expected)}"
        )


def test_every_e2e_lane_is_pinned_here() -> None:
    """A lane added later must not ship unpinned — the defect class above repeats once per lane.

    `LANES` is a closed literal, so a fourth lane (Flutter is the next backend planned) could land its
    own aggregator listing `changes` in `needs:` and never reading it, and every test here would pass,
    never having opened that file. Deriving the lane set would defeat the point; failing until the new
    lane is listed puts the wiring in front of its author.
    """
    on_disk = {path.name.removesuffix("-e2e.yml") for path in WORKFLOWS.glob("*-e2e.yml")}
    assert on_disk == set(LANES), f"lanes not pinned: {sorted(on_disk - set(LANES))}"
    assert set(GATED) == set(LANES), f"no gate pinned for: {sorted(set(LANES) - set(GATED))}"


def test_nothing_on_the_verdict_path_disarms_itself_with_continue_on_error() -> None:
    """`continue-on-error` reports success for a failure, and no assertion above would notice.

    On a gated job it hands `needs:` a `success` the job never earned; on the aggregator, or on the
    aggregator's own verdict step, it reports the required check green over a script that exited
    non-zero. Any of the three leaves every other assertion here intact while the gate stops meaning
    anything — the permanently green required check this file exists to rule out, reached from the
    other side, and a plausible one-line edit under pressure to unblock a merge. Nothing on the
    verdict path sets the key today; the step-level ones that exist sit on diagnostics steps, whose
    failure is beside the point of the job they run in.
    """
    for lane in LANES:
        jobs = _jobs(lane)
        aggregator = _aggregator(lane)
        for job_id in aggregator["needs"]:
            assert not _disarms_its_own_failure(jobs[job_id]), (lane, job_id)
        assert not _disarms_its_own_failure(aggregator), lane
        assert not _disarms_its_own_failure(_check_step(aggregator)), lane


def test_the_verdict_script_reddens_the_gate_for_every_dependency() -> None:
    """Run the real script: each dependency's failure must exit non-zero, and a skip must not.

    The structural tests above are satisfied by a loop comparing against a value no job ever reports
    — a permanently green required check. This one asks the script itself, once per dependency per
    result, which is also what proves a newly-gated job is wired into the verdict rather than merely
    named in `env:`.

    Run under `-e`, because that is the shell Actions gives a `run:` step (`bash -e {0}`; none of the
    three lanes overrides `shell:`). Without it the fidelity this layer rests on is one-directional in
    the dangerous direction: a later verdict script whose intermediate command can fail would exit
    non-zero on every real run while still passing here.

    Extend the real environment rather than replacing it, for the same reason: Actions hands a `run:`
    step the runner's whole environment plus the step's `env:`, which is what `os.environ | env`
    models. Passing `env` alone leaves the script with no `PATH` — bash then falls back to a
    compiled-in default that happens to resolve `grep` and `jq`, but not a tool installed anywhere
    else, and that carries `.` on the search path where the runner's does not.
    """
    for lane in LANES:
        step = _check_step(_aggregator(lane))
        for var in _read_jobs(step):
            for result, must_redden in RESULTS.items():
                env = dict.fromkeys(_read_jobs(step), "success") | {var: result}
                completed = subprocess.run(
                    ["bash", "-e", "-c", step["run"]],
                    env=os.environ | env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert (completed.returncode != 0) is must_redden, (
                    f"{lane}: with {var}={result} the gate exited {completed.returncode}, "
                    f"expected {'non-zero' if must_redden else 'zero'}\n{completed.stdout}"
                )
