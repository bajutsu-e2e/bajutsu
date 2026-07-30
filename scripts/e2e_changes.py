#!/usr/bin/env python3
"""Decide whether a PR warrants a backend's on-device / real-browser E2E jobs (the `changes` job).

The three E2E lanes — ios-e2e.yml (macOS / idb + XCUITest), android-e2e.yml (Linux / adb under KVM),
and web-e2e.yml (Linux / Playwright) — each carry a required aggregator check (`E2E`, `E2E (android)`,
`E2E (web)`). A required check that never reports blocks a merge, so none of them can be path-gated at
the workflow trigger; instead every lane triggers on every PR and this module decides, per lane,
whether the heavy jobs actually run. The aggregator always reports (a path-skip is a pass), so an
unrelated PR is neither run nor blocked. This module is the single source of truth for that decision,
split into two testable pieces:

- ``changed_files`` lists the PR's *own* changes with a **three-dot** diff (``git diff base...head``,
  i.e. from the merge base of the two commits to ``head``). ``base`` is the base-branch tip, so a
  two-dot ``git diff base head`` compares the tips directly: when ``base`` has advanced past the
  PR's fork point it reports every file main touched meanwhile as "changed". An unrelated
  ``bajutsu/runner/…`` commit on main would then trip the filter and burn the metered jobs on, say,
  a roadmap-only PR. The merge-base diff yields only what the PR itself changed.

- ``is_relevant`` is the positive-list, keyed by lane. Every lane shares ``_RUN_PATH`` — the
  run / codegen / record importable surface each backend exercises — and adds its own driver, app,
  scenarios, conformance harness, and workflow file. Subpackages are swept; top-level ``bajutsu/*.py``
  modules are allow-listed by name — only the ones that path actually imports — because the top level
  also holds serve/analytics/crawl modules (stats, audit, coverage, usage*, crawl*, alerts, github,
  …) the E2E never touches; a bare ``bajutsu/*.py`` glob swept those in and burned the jobs on, e.g.,
  a serve-only PR. The two per-backend directories — ``bajutsu/drivers/`` and
  ``bajutsu/platform_lifecycle/environments/`` — are swept by the shared core minus exactly the leaves
  each lane claims by name, so a lane fires only on the driver and environment its own backend
  imports, while no file is orphaned: anything unclaimed, a new module included, fires all three. A new
  top-level ``bajutsu/*.py`` module or CLI command still defaults to NOT triggering — add its pattern
  to ``_RUN_PATH`` (all lanes) or the lane's own fragment. Allow-listing by name carries two hazards
  the tests now guard: a name anchored with ``\\.py$`` stops matching the day its module becomes a
  package (see ``_RUN_PATH_MODULES``), and a renamed or deleted path leaves its pattern matching
  nothing. Either one silently stops a lane from firing, so both fail ``make check`` instead.

Invoked by each workflow with ``BASE_SHA`` / ``HEAD_SHA`` in the environment and ``E2E_LANE`` naming
the lane (``ios`` — the default — / ``android`` / ``web``); it writes ``relevant=true|false`` to
``GITHUB_OUTPUT``. An empty ``BASE_SHA`` (a manual ``workflow_dispatch`` with no PR context) always
counts as relevant.

BE-0322 narrows the fan-out for the one case it can prove safe: a change confined to a lane's
scenario files fires only the jobs that declare a changed scenario, rather than the whole lane.
Alongside ``relevant`` the module emits two more outputs the lane's jobs read: ``shared=true|false``
(a shared-code change — driver / runner / app / workflow code that can affect any scenario, so the
whole lane fires) and ``affected`` (a JSON array of the scenario-keyed jobs a scenario-only change
reached). A scenario-keyed job runs when ``relevant`` is true and (``shared`` is true, or the job is
in ``affected``); a dimension job that declares no scenario (codegen / conformance / visual) runs
whenever ``relevant`` is true. The decision over-selects toward the whole lane — a shared-code
change, an unattributable scenario fragment, an unreadable workflow, and a lane with no
scenario-keyed jobs (Android, web) all fall back to ``shared`` — so it never skips a job a change
could have broken. It reads only the ``git`` diff and the ``scenarios:`` each job already declares:
no large language model touches the decision, and it has no bearing on any run's pass/fail verdict.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

# The run / codegen / record importable surface every backend's E2E exercises — identical across the
# iOS, Android, and web lanes, so it lives here once. Subpackages (runner / scenario / orchestrator /
# codegen) are swept; top-level modules are allow-listed by name because the top level also holds the
# serve/analytics/crawl modules (stats, audit, coverage, usage*, crawl*, alerts, notify, github, the
# AI/enrich/triage helpers) that never run here — a bare `bajutsu/*.py` glob burned the jobs on a
# serve-only PR. `crawl` is swept only for the three modules record's package re-export pulls in
# (`__init__` imports `core` and `serialize`); its guide/report/repro/flows/tabs siblings are
# periphery the run never imports. `assertions` is a package (BE-0250) whose every module is on the
# run path, so the whole package is swept. A new top-level module defaults to NOT triggering — add it
# here (all lanes) or to a lane fragment below.
#
# Each name in `_RUN_PATH_MODULES` is matched with a trailing `\.py$`, so it reaches a single-file
# module and nothing else: the day that module becomes a package, its every file stops matching and
# the lane silently stops firing. That drift hit `config` (BE-0252) and `platform_lifecycle`
# unnoticed, so both now live in the swept-package group below. The names are a tuple rather than
# regex text so `test_no_by_name_module_is_actually_a_package` can check each one against the tree
# and fail the gate on the PR that does the next such split.
_RUN_PATH_MODULES = (
    "_yaml",
    "adb",
    "artifact_perms",
    "backends",
    "capabilities",
    "capability_preflight",
    "config_source",
    # The platform-neutral `DeviceError` base (BE-0260) both `run` and the doctor gate catch to turn
    # a device fault into their verdict, so a change to it can change what either reports.
    "device_errors",
    "device_id",
    # `doctor` and `preflight` are the onboarding gate every lane runs as `bajutsu doctor
    # --environment-only` (BE-0304), asserted by `scripts/assert_doctor_env.py` above. The assertion
    # script was on this list from the start; the code it asserts against was not.
    "doctor",
    "dom",
    "dotenv",
    "elements",
    "handoff",
    "interp",
    "mailbox",
    "preflight",
    "record",
    "run_id",
    "screenshots",
    "simctl",
    "totp",
    "web_network",
    "webview",
)

_RUN_PATH = (
    r"bajutsu/(?:runner|scenario|orchestrator|codegen|config)/"
    # Everything in the lifecycle package except the four per-backend `Environment` leaves, which
    # each lane claims by name below (the `bajutsu/drivers/` contract, one layer up). The carve-out
    # names only the leaves that exist: a new `environments/<foo>.py` falls through to this sweep and
    # fires every lane — an over-fire, the safe direction — until a lane claims it.
    r"|bajutsu/platform_lifecycle/(?!environments/(?:android|web|xcuitest|xcuitest_live)\.py$)"
    r"|bajutsu/(?:" + "|".join(_RUN_PATH_MODULES) + r")\.py$"
    r"|bajutsu/crawl/(?:core|serialize|__init__)\.py$"
    r"|bajutsu/agents/(?:protocols|__init__)\.py$"
    r"|bajutsu/evidence/(?:core|intervals|network|visual|golden|redaction|__init__)\.py$"
    r"|bajutsu/assertions/"
    # Every driver file except the per-backend leaves each lane claims by name below. The invariant is
    # that no driver file is orphaned: a file is either claimed by exactly the lanes whose backend
    # imports it, or shared by all three. `base.py` (Point/Element/Selector, the Driver Protocol,
    # selector resolution) and `__init__.py` are universal because every backend's driver imports them;
    # `fake.py` no lane drives, but sweeping it costs an over-fire, while orphaning it would be the
    # silent under-trigger this sweep exists to prevent. A new `drivers/<foo>.py` lands here too and
    # fires every lane until a lane claims it — the safe direction.
    r"|bajutsu/drivers/(?!(?:adb|coordinate_tree|playwright|xcuitest|xcuitest_live)\.py$)"
    r"|bajutsu/cli/__init__\.py$"
    r"|bajutsu/cli/_shared\.py$"
    r"|bajutsu/cli/commands/__init__\.py$"
    r"|bajutsu/cli/commands/run\.py$"
    # The `doctor` CLI command each lane's BE-0304 onboarding gate invokes, alongside the
    # `bajutsu/doctor.py` core it renders from (both above). Its AI-availability half
    # (`agents/availability`, `ai/credential_gap`) stays excluded: `assert_doctor_env.py` reads only
    # the `environment:` section, which no AI credential can move.
    r"|bajutsu/cli/commands/doctor\.py$"
    r"|tests/driver_conformance\.py$"
    # The onboarding-gate assertion each lane's `doctor` step runs (BE-0304); a change to it must
    # re-run every lane that exercises it, so it lives in the shared core, not one lane fragment.
    r"|scripts/assert_doctor_env\.py$"
    # This module itself. A change to the gate that decides which lanes run must run every lane, or
    # the new decision ships without a single lane ever having exercised it — the same silent
    # under-trigger the rest of this list guards against, one level up. Such a change is rare, so
    # firing all three lanes on it is cheap next to shipping the gate unvalidated.
    r"|scripts/e2e_changes\.py$"
    r"|pyproject\.toml$"
    r"|uv\.lock$"
)

# Each lane adds its own driver, app, scenarios, conformance harness, and workflow file on top of
# `_RUN_PATH`. The lane differences are real: iOS and web relay both codegen and record CLI
# commands, the Android lane relays `bajutsu run` and (for its `uiautomator (codegen)` job, BE-0294)
# `bajutsu codegen` but not `record`; each lane touches only the driver module(s) its own backend
# actually imports (iOS: xcuitest[_live].py, Android: adb.py, web: playwright.py — verified against
# each module's own imports, not a blanket `bajutsu/drivers/` sweep, which previously fired a lane's
# metered jobs on another lane's driver-only change); each lane owns its showcase surface, its
# conformance harness module, and its own workflow file.
#
# Both `bajutsu/drivers/` and `bajutsu/platform_lifecycle/environments/` are split this way, and both
# default to over-firing: the shared core sweeps each directory minus exactly the leaves named here,
# so a file a lane claims fires only that lane, while anything unclaimed — including a newly added
# module — fires all three. Naming the leaves in a lane fragment therefore narrows a known file; it
# never decides whether a new file is seen at all. An earlier revision inverted that default and
# allow-listed each driver by name, which meant a new `drivers/<foo>.py` fired nothing and silently
# under-triggered every lane's required check.
_LANE_PATHS: dict[str, str] = {
    "ios": (
        r"|bajutsu/drivers/(?:xcuitest|xcuitest_live)\.py$"
        # The XCUITest lifecycle environments (cold spawn, the warm resident lease, the BE-0292
        # bundled runner) — the iOS half of the `platform_lifecycle/` carve-out above.
        r"|bajutsu/platform_lifecycle/environments/(?:xcuitest|xcuitest_live)\.py$"
        r"|bajutsu/cli/commands/(?:codegen|record)\.py$"
        r"|tests/test_driver_conformance_ondevice\.py$"
        r"|BajutsuKit/"
        r"|demos/showcase/ios/swiftui/"
        r"|demos/showcase/ios/uikit/"
        # The main config and the BE-0292 bundled-runner config the `bundled-runner (xcuitest)` job runs.
        r"|demos/showcase/showcase(?:\.[^/]+)?\.config\.yaml$"
        r"|demos/showcase/scenarios/"
        r"|Makefile$"
        # The showcase's own Makefile (`e2e-visual` and friends) — the top-level `Makefile$` above is
        # anchored to the repo root and doesn't reach this one, but the `visual` job depends on it.
        r"|demos/showcase/Makefile$"
        r"|\.github/workflows/ios-e2e\.yml$"
        r"|\.github/actions/bajutsu-e2e/"
        r"|\.github/actions/boot-simulator/"
    ),
    "android": (
        # Only the adb driver and the Python side of the resident UI Automator channel (BE-0245) this
        # lane exercises. coordinate_tree.py is adb.py's own read/settle core (BE-0254) — a change to
        # it can change adb's runtime behavior even though adb.py itself is untouched.
        r"|bajutsu/drivers/adb\.py$"
        r"|bajutsu/drivers/coordinate_tree\.py$"
        r"|bajutsu/adb_resident\.py$"
        # The Android lifecycle environment (boot, install, the BE-0236 provision profile) — the
        # Android half of the `platform_lifecycle/` carve-out.
        r"|bajutsu/platform_lifecycle/environments/android\.py$"
        # The `uiautomator (codegen)` job (BE-0294) regenerates its test with `bajutsu codegen`, so a
        # change to that CLI command is android-relevant — unlike `bajutsu run`, which the other jobs
        # drive (the shared `_RUN_PATH` already sweeps the `bajutsu/codegen/` emitter package itself).
        r"|bajutsu/cli/commands/codegen\.py$"
        r"|demos/showcase/android/"
        r"|demos/showcase/scenarios/"
        r"|demos/showcase/showcase\.config\.yaml$"
        r"|BajutsuAndroid/"  # the app-side clipboard SDK the showcase APKs build in (BE-0233)
        r"|BajutsuAndroidUIAutomatorServer/"  # the resident server this lane builds + exercises (BE-0245)
        r"|tests/test_driver_conformance_ondevice_android\.py$"
        r"|\.github/workflows/android-e2e\.yml$"
    ),
    "web": (
        r"|bajutsu/drivers/playwright\.py$"
        # The web lifecycle environment (browser launch, context teardown) — the web half of the
        # `platform_lifecycle/` carve-out.
        r"|bajutsu/platform_lifecycle/environments/web\.py$"
        # The real provisioner the `onboarding (doctor / provision)` job runs as `python -m
        # bajutsu.provision --backend web` (BE-0304) to install Chromium for real. Web-only: no other
        # lane invokes it, and the lanes never run `scripts/install.sh`, its other caller.
        r"|bajutsu/provision\.py$"
        r"|bajutsu/cli/commands/(?:codegen|record)\.py$"
        # The serve-UI dogfood (BE-0058) drives the served SPA, so the serve backend and its templates
        # are web-CI-relevant whenever they change, not only when the harness itself does.
        r"|bajutsu/serve/"
        r"|bajutsu/templates/"
        r"|demos/serve-ui/"
        r"|demos/web/"
        r"|tests/test_driver_conformance_web\.py$"
        r"|Makefile$"
        r"|\.github/workflows/web-e2e\.yml$"
    ),
}

# One path is enough to trigger; anchored at the start of each path. Compiled once per lane so the
# positive-list reads as a single source of truth.
_LANE_RE: dict[str, re.Pattern[str]] = {
    lane: re.compile(r"^(?:" + _RUN_PATH + extra + r")") for lane, extra in _LANE_PATHS.items()
}

DEFAULT_LANE = "ios"

# The scenario-file subset of each lane's relevant surface (BE-0322). A change confined to these
# files is `scenario-only` and can be narrowed to the jobs that declare a changed scenario; a
# relevant change anywhere else is `shared` and fires the whole lane. The showcase scenarios are the
# iOS and Android scenario files; the web lane's scenarios live under its own demos. Each pattern is
# a subset of the lane's own `_LANE_PATHS` fragment above, so a scenario file is always relevant first.
_LANE_SCENARIO_PATHS: dict[str, str] = {
    "ios": r"demos/showcase/scenarios/",
    "android": r"demos/showcase/scenarios/",
    "web": r"demos/serve-ui/|demos/web/",
}

_LANE_SCENARIO_RE: dict[str, re.Pattern[str]] = {
    lane: re.compile(r"^(?:" + pattern + r")") for lane, pattern in _LANE_SCENARIO_PATHS.items()
}

# Repo root, resolved from this file so the workflow read below works regardless of the invoking cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# A `jobs:` block header, a job id (a 2-space-indented key under it), and a `scenarios:` input line
# (nested deeper inside a step's `with:`). `scenarios:` appears only as the bajutsu-e2e action's
# input in these workflows, so every match is a job's declared scenario (BE-0322). A line scan, not
# a YAML parse, keeps the `changes` job on the standard library — its bare `python3` has no PyYAML.
_JOBS_HEADER_RE = re.compile(r"^jobs:\s*(?:#.*)?$")
# A column-0 key ends the jobs block — but not a column-0 comment, which would otherwise cut the scan
# short and silently drop every job below it (an under-fire that could skip a job exercising a
# changed scenario). Comments and blank lines are skipped, so only a real top-level key stops it.
_TOP_LEVEL_KEY_RE = re.compile(r"^[^\s#]")
_JOB_ID_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$")
_SCENARIOS_INPUT_RE = re.compile(r"^\s+scenarios:\s*(.*?)\s*(?:#.*)?$")
# A single unquoted scenario path — the only form these workflows use. A `scenarios:` value that is
# quoted, a block scalar (`|` / `>`), or a multi-item list matches neither and is rejected below, so
# the scanner fails loud into the caller's whole-fleet fallback rather than mis-parsing it silently.
_PLAIN_PATH_RE = re.compile(r"^[\w./-]+$")


def _lane_re(lane: str) -> re.Pattern[str]:
    """The compiled relevance pattern for ``lane``.

    Raises:
        ValueError: ``lane`` is none of the known lanes. ``E2E_LANE`` is a literal each workflow
            hard-codes, not user input, so an unrecognized value is a config bug — it must fail the
            `changes` job loudly rather than silently substitute another lane's filter, which could
            under-trigger and let a required aggregator report green without exercising this lane.
    """
    try:
        return _LANE_RE[lane]
    except KeyError:
        raise ValueError(f"Unknown E2E lane {lane!r}; expected one of {sorted(_LANE_RE)}") from None


def is_relevant(paths: Iterable[str], lane: str = DEFAULT_LANE) -> bool:
    """Whether any changed path is one the given lane's E2E jobs actually exercise.

    Raises:
        ValueError: ``lane`` is none of the known lanes (see ``_lane_re``).
    """
    pattern = _lane_re(lane)
    return any(pattern.match(p) for p in paths)


def classify_change(paths: Iterable[str], lane: str = DEFAULT_LANE) -> str:
    """Partition ``lane``'s changed files into ``none`` / ``scenario-only`` / ``shared`` (BE-0322).

    Returns:
        ``none`` when no changed path is one the lane exercises (skip it, as today); ``scenario-only``
        when every relevant path is a scenario file (the affected jobs can be narrowed); ``shared``
        when a relevant path lies outside the scenario files (shared code that can affect any
        scenario — fire the whole lane). Irrelevant paths (a doc, a roadmap file) are ignored, so
        they never tip a scenario-only change into ``shared``.

    Raises:
        ValueError: ``lane`` is none of the known lanes (see ``_lane_re``).
    """
    pattern = _lane_re(lane)
    relevant = [p for p in paths if pattern.match(p)]
    if not relevant:
        return "none"
    scenario_re = _LANE_SCENARIO_RE[lane]
    return "scenario-only" if all(scenario_re.match(p) for p in relevant) else "shared"


def job_scenario_map(workflow_text: str) -> dict[str, set[str]]:
    """Map each job to the scenario files it declares, read from a lane's workflow file (BE-0322).

    Reads the ``scenarios:`` inputs already present in the workflow, so the map is a lookup over the
    workflow's own declarations rather than a second list to maintain — it cannot drift from what
    each job runs. A job that declares no scenario — a dimension job (codegen / conformance /
    visual), or a lane with no scenario-keyed jobs at all — is simply absent, so an empty map is
    valid. A line scan keeps the caller on the standard library (the ``changes`` job runs a bare
    ``python3`` with no PyYAML); the format is regular block YAML pinned by the tests.

    Raises:
        ValueError: the text has no ``jobs`` block; a ``scenarios:`` value is a quoted path, a list,
            or a literal block scalar (``|``); or a path in a folded block scalar (``>``/``>-``) is
            not a plain unquoted path. The caller falls back to firing the whole lane rather than
            trusting a mis-parsed map that would narrow the lane wrongly.
    """
    result: dict[str, set[str]] = {}
    in_jobs = False
    current_job: str | None = None
    # When non-None, we're collecting path lines from a folded block scalar (`>-`/`>`).
    # Stores the job key and the indentation of the `scenarios: >-` line.
    block_collecting: str | None = None
    block_indent: int = 0
    for line in workflow_text.splitlines():
        if block_collecting is not None:
            stripped = line.rstrip()
            if not stripped or stripped.lstrip().startswith("#"):
                continue  # blank / comment inside block scalar
            indent = len(stripped) - len(stripped.lstrip())
            if indent > block_indent:
                path = stripped.strip()
                if not _PLAIN_PATH_RE.match(path):
                    raise ValueError(
                        f"unparseable path {path!r} in block-scalar `scenarios:` of job {block_collecting!r}"
                    )
                result.setdefault(block_collecting, set()).add(path)
                continue
            block_collecting = None  # shallower indent: block scalar ended; fall through
        if _JOBS_HEADER_RE.match(line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if _TOP_LEVEL_KEY_RE.match(line):
            break  # a new column-0 key ends the jobs block
        if job_match := _JOB_ID_RE.match(line):
            current_job = job_match.group(1)
        elif current_job is not None and (scenario_match := _SCENARIOS_INPUT_RE.match(line)):
            value = scenario_match.group(1)
            if value in (">-", ">"):
                block_collecting = current_job
                block_indent = len(line) - len(line.lstrip())
            elif _PLAIN_PATH_RE.match(value):
                result.setdefault(current_job, set()).add(value)
            else:
                raise ValueError(f"unparseable `scenarios:` value {value!r} in job {current_job!r}")
    if not in_jobs:
        raise ValueError("workflow YAML has no `jobs` mapping")
    return result


def affected_jobs(changed_scenarios: Iterable[str], job_map: dict[str, set[str]]) -> set[str]:
    """The jobs whose declared scenarios intersect the changed scenario files.

    A scenario reused across jobs (``smoke.yaml``, declared by both ``run`` and ``bundled-runner``)
    selects every job that declares it, because each one exercises it.
    """
    changed = set(changed_scenarios)
    return {job for job, scenarios in job_map.items() if scenarios & changed}


def lane_workflow_text(lane: str) -> str | None:
    """The text of ``lane``'s E2E workflow file, or None when it is absent."""
    path = _REPO_ROOT / ".github" / "workflows" / f"{lane}-e2e.yml"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _affected_or_fallback(changed: list[str], lane: str) -> list[str] | None:
    """The scenario-keyed jobs a scenario-only change reached, or None to fire the whole lane.

    None means fall back to the whole fleet — the safe over-selection — when the lane's workflow
    can't be read or parsed, or when a changed scenario is not attributable to any job (a shared
    fragment or a scenario no job runs). A lane with no scenario-keyed jobs (Android, web) lands here
    too: its map is empty, so every changed scenario is unattributable and the whole lane fires.
    """
    text = lane_workflow_text(lane)
    if text is None:
        return None
    try:
        job_map = job_scenario_map(text)
    except ValueError:
        return None
    scenario_re = _LANE_SCENARIO_RE[lane]
    changed_scenarios = {p for p in changed if scenario_re.match(p)}
    # An empty `job_map` (a lane with no scenario-keyed jobs) unions to the empty set, so every
    # changed scenario is unattributable and the whole lane fires — the safe over-selection.
    declared = set().union(*job_map.values())
    if changed_scenarios - declared:  # a changed scenario no job declares → fire the whole lane
        return None
    return sorted(affected_jobs(changed_scenarios, job_map))


def changed_files(base: str, head: str) -> list[str]:
    """The PR's own changed files, via a merge-base (three-dot) diff of ``base`` and ``head``."""
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def _emit(relevant: bool, shared: bool, affected: list[str]) -> None:
    """Print the verdict and append it to ``GITHUB_OUTPUT`` when the workflow provides one.

    Emits the three outputs the lane's jobs read (BE-0322): ``relevant`` (run any metered job at
    all), ``shared`` (a shared-code change — fire the whole lane), and ``affected`` (a JSON array of
    the scenario-keyed jobs a scenario-only change reached; empty unless the change was narrowed).
    """
    lines = [
        f"relevant={str(relevant).lower()}",
        f"shared={str(shared).lower()}",
        f"affected={json.dumps(affected)}",
    ]
    for line in lines:
        print(line)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main() -> int:
    lane = os.environ.get("E2E_LANE", DEFAULT_LANE)
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("HEAD_SHA", "")
    if not base:
        # workflow_dispatch: no PR context, so nothing to path-gate against — run the whole lane.
        _emit(relevant=True, shared=True, affected=[])
        return 0

    changed = changed_files(base, head)
    print(f"Lane: {lane}")
    print("Changed files:")
    for path in changed:
        print(f"  {path}")

    kind = classify_change(changed, lane)
    if kind == "none":
        _emit(relevant=False, shared=False, affected=[])
        return 0

    # A scenario-only change narrows to the jobs that declare a changed scenario; anything else — a
    # shared-code change, or a scenario-only change the workflow can't attribute (`None`) — fires the
    # whole lane, the safe over-selection.
    affected = _affected_or_fallback(changed, lane) if kind == "scenario-only" else None
    _emit(relevant=True, shared=affected is None, affected=affected or [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
