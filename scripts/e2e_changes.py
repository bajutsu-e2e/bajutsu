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

- ``is_relevant`` is keyed by lane. Every lane shares ``_RUN_PATH`` — the run / codegen / record
  surface each backend exercises — and adds its own driver, app, scenarios, conformance harness, and
  workflow file. BE-0333 inverts the default for ``bajutsu/``: the shared core sweeps the *whole*
  package and carves out only what is explicitly classified out — the periphery no lane exercises
  (``_PERIPHERY_EXCLUSIONS``, each entry with the reason it is out) and the per-backend leaves each
  lane reclaims (``_LANE_CLAIMED``). A file named in neither is swept in and fires all three lanes,
  so a new top-level ``bajutsu/*.py`` module or CLI command over-fires — a wasted job, the safe
  direction — instead of the silent under-trigger the old hand-kept positive list produced when a
  listed module became a package (``config``, BE-0252; ``platform_lifecycle``) or a run-path file
  went unlisted. A change confined to the periphery still fires nothing. The two per-backend
  directories — ``bajutsu/common/drivers/`` and ``bajutsu/platform_lifecycle/environments/`` — follow the
  same shape: swept by the shared core minus exactly the leaves each lane claims, so a lane fires
  only on the driver and environment its own backend imports, while no file is orphaned. A renamed or
  deleted path leaves its pattern matching nothing, so the tests check every literal path and
  exclusion entry against the tree and fail ``make check`` on the PR that moves one.

Invoked by each workflow with ``BASE_SHA`` / ``HEAD_SHA`` in the environment and ``E2E_LANE`` naming
the lane (``ios`` — the default — / ``android`` / ``web``); it writes ``relevant=true|false`` to
``GITHUB_OUTPUT``. An empty ``BASE_SHA`` (a manual ``workflow_dispatch`` with no PR context) always
counts as relevant.

A fourth output, ``pool``, keys the concurrent-device job BE-0298 adds. That job boots **two** real
devices, which is the lane's most expensive and most environment-sensitive work, and no other job can
observe what it checks — every other job boots one device, so the pool's cross-worker isolation is
invisible to it. So it is keyed on a surface narrower than ``shared``: the code deciding how a run
splits across devices and where each worker's evidence lands (``touches_pool``). It over-selects the
same way every filter here does, and it is a conjunction with ``relevant``, so a change to the *other*
lane's workflow never fires this one's pool job. Only the Android lane still has such a job — the iOS
half was withdrawn (BE-0298), since two booted Simulators exhaust the hosted macOS runner — so the
iOS and web lanes emit the output and leave it unread.

BE-0322 narrows the fan-out for the one case it can prove safe: a change confined to a lane's
scenario files fires only the jobs that declare a changed scenario, rather than the whole lane.
Alongside ``relevant`` the module emits two more outputs the lane's jobs read: ``shared=true|false``
(a shared-code change — driver / runner / app / workflow code that can affect any scenario, so the
whole lane fires) and ``affected`` (a JSON array of the scenario-keyed jobs a scenario-only change
reached). A scenario-keyed job runs when ``relevant`` is true and (``shared`` is true, or the job is
in ``affected``); a dimension job that declares no scenario runs whenever ``relevant`` is true. On
iOS the ``codegen``, ``visual``, and ``network`` jobs are scenario-keyed too (BE-0338): they declare
their scenarios in ``demos/showcase/Makefile`` targets rather than a workflow input, so
``job_scenario_map`` never sees them — ``lane_job_scenario_map`` folds those Makefile-declared
scenarios into the map. What the ``affected`` narrowing then leaves on iOS is ``conformance`` and
``fault-injection``, which declare no scenario subset (each drives its own whole suite) and so fire
whenever ``relevant`` is true; and ``build``, which stages the products every consumer job installs,
on the same bare guard. The decision over-selects toward the whole lane — a shared-code change, an
unattributable scenario fragment, an unreadable workflow, and a lane with no scenario-keyed jobs
(Android, web) all fall back to ``shared`` — so it never skips a job a change could have broken. It
reads only the ``git`` diff, the ``scenarios:`` each job declares, and (for the iOS Makefile-declared
jobs) the showcase Makefile targets those jobs run: no large language model touches the decision, and
it has no bearing on any run's pass/fail verdict.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

# The run / codegen / record surface every backend's E2E exercises — identical across the iOS,
# Android, and web lanes, so it lives here once. BE-0333 inverts the default for `bajutsu/`: rather
# than a hand-kept positive list of the modules the run path imports — which silently under-fired the
# day a listed module became a package (`config`, BE-0252; `platform_lifecycle`) or a new run-path
# file or CLI command was added — the shared core sweeps the whole package and carves out only what
# is explicitly classified out. A file named nowhere below is swept in and fires all three lanes
# until somebody classifies it: a wasted job, the safe direction, rather than an unexercised required
# check that reports green without running the change at all.
#
# Two exclusion sets carve files out of that sweep:
#
# - `_PERIPHERY_EXCLUSIONS` — files under `bajutsu/` no E2E lane exercises (the analytics / analysis
#   stacks, the MCP server, the AI adapters, the GitHub and cloud integrations, and the individual
#   periphery modules of the mixed `agents` / `crawl` / `cli.commands` packages whose siblings do
#   run). Each carries the reason it is out, so every deliberate "the E2E never runs this" decision
#   reads as a decision in one place (BE-0333 Unit 3 folds the former ad-hoc parity tests in here).
#   `test_periphery_exclusions_fire_no_lane` pins that none of them fire; the Unit 2 closure check
#   pins that the list stays complete as the run path's imports change.
#
# - `_LANE_CLAIMED` — per-backend leaves the shared core must NOT fire on every lane, each re-added
#   by the lane fragment(s) whose backend imports it (the four driver leaves, the four lifecycle
#   `Environment` leaves, the serve backend + templates the web dogfood drives, the provisioner, the
#   resident-channel Python side, and the `record` CLI command the Android lane does not run).
#   Sweeping these into the shared core would fire, e.g., the macOS jobs on an adb-only change no
#   XCUITest backend imports — the regression PR #1405 fixed.
_PERIPHERY_EXCLUSIONS: tuple[tuple[str, str], ...] = (
    (
        "bajutsu/ai/",
        "the AI provider adapters — no run / codegen / record path imports them for a run's verdict",
    ),
    (
        "bajutsu/analytics/",
        "the analytics ledger the report writer feeds; a run writes it but never asserts on it",
    ),
    (
        "bajutsu/analysis/",
        "the offline analysis stack (audit / coverage / stats) a serve or CLI report renders",
    ),
    (
        "bajutsu/mcp/",
        "the Model Context Protocol server — a separate transport, never on the on-device run path",
    ),
    (
        "bajutsu/github/",
        "the GitHub App / Actions integration, a hosted-CI concern the run never drives",
    ),
    (
        "bajutsu/cloud/",
        "the AWS Device Farm submitter — a hosted batch path, not the local on-device run",
    ),
    (
        "bajutsu/notify.py",
        "run-completion notifications (serve / CI glue), never exercised by a run itself",
    ),
    (
        "bajutsu/triage.py",
        "the AI triage command's core — an authoring / diagnosis path, not a run",
    ),
    (
        "bajutsu/trace.py",
        "the `bajutsu trace` diagnostic — it inspects a past run, it is never part of one",
    ),
    # agents/: record imports the Agent / EnrichmentAgent *protocols* (`agents/protocols.py`, swept in
    # below), but a run drives no live agent, so the concrete implementations stay out. The factory
    # entry restates the former `test_agent_factory_is_not_relevant_by_parity` (BE-0333 Unit 3).
    (
        "bajutsu/agents/factory.py",
        "the agent factory record imports at authoring time; a run drives no live agent",
    ),
    (
        "bajutsu/agents/ai_config.py",
        "AI model / credential configuration, an authoring-path concern",
    ),
    ("bajutsu/agents/alerts.py", "serve-side alerting over a run's results, not part of the run"),
    (
        "bajutsu/agents/anthropic_client.py",
        "the Anthropic API client the live agents use; a run drives none",
    ),
    (
        "bajutsu/agents/availability.py",
        "the AI-credential probe doctor's AI half reports; the E2E doctor gate reads only its environment section",
    ),
    (
        "bajutsu/agents/claude.py",
        "a concrete Claude agent implementation, an authoring / record-proposal path",
    ),
    ("bajutsu/agents/claude_backed.py", "a concrete Claude-backed agent base, authoring-path only"),
    (
        "bajutsu/agents/claude_enrich.py",
        "Claude-backed evidence enrichment, a post-run authoring path",
    ),
    ("bajutsu/agents/claude_triage.py", "Claude-backed triage, a diagnosis path off the run"),
    ("bajutsu/agents/enrich.py", "the enrichment-agent surface a run never invokes"),
    # crawl/: record imports the crawl engine core (`core` / `serialize` / `__init__`, swept in); the
    # guide / report / repro / flows / tabs siblings are periphery the run never imports.
    ("bajutsu/crawl/guide.py", "crawl's human-facing guide output, an authoring path"),
    ("bajutsu/crawl/report.py", "crawl's report renderer, a post-crawl authoring path"),
    ("bajutsu/crawl/repro.py", "crawl's repro-scenario emitter, an authoring path"),
    ("bajutsu/crawl/flows.py", "crawl's flow-analysis helpers, an authoring path"),
    ("bajutsu/crawl/tabs.py", "crawl's tab-tracking helpers, an authoring path"),
    # cli/commands/: `run` / `codegen` / `record` / `doctor` are the four the lanes drive (swept in,
    # `record` via `_LANE_CLAIMED`); the rest are serve / analysis / authoring commands no lane runs.
    ("bajutsu/cli/commands/approve.py", "the baseline-approval command, an authoring path"),
    ("bajutsu/cli/commands/audit.py", "the audit-report command, an analysis path"),
    ("bajutsu/cli/commands/coverage.py", "the coverage-report command, an analysis path"),
    ("bajutsu/cli/commands/crawl.py", "the crawl command, an authoring path"),
    ("bajutsu/cli/commands/export.py", "the export command, a reporting path"),
    ("bajutsu/cli/commands/flakiness.py", "the flakiness-report command, an analysis path"),
    ("bajutsu/cli/commands/impact.py", "the impact-report command, an analysis path"),
    (
        "bajutsu/cli/commands/lint.py",
        "the scenario-lint command, a static-check path no run drives",
    ),
    ("bajutsu/cli/commands/mcp.py", "the MCP-server command, a separate transport"),
    ("bajutsu/cli/commands/report.py", "the report command, a reporting path"),
    ("bajutsu/cli/commands/schema.py", "the schema-dump command, a tooling path"),
    (
        "bajutsu/cli/commands/serve.py",
        "the serve launcher; the web lane exercises the served backend, not this command",
    ),
    ("bajutsu/cli/commands/stats.py", "the stats command, an analysis path"),
    ("bajutsu/cli/commands/trace.py", "the trace command, a diagnostic path"),
    ("bajutsu/cli/commands/triage.py", "the triage command, a diagnosis path"),
    ("bajutsu/cli/commands/worker.py", "the serve-worker command, a serve runtime path"),
)

# Per-backend leaves the shared core sweeps out and each lane fragment re-adds, so a leaf fires only
# the lane(s) whose backend imports it, never all three. `test_lane_claimed_leaves_fire_a_lane` pins
# that each stays claimed by at least one lane (none orphaned by this exclusion), and the per-lane
# surface tests pin which lane(s).
_LANE_CLAIMED: tuple[str, ...] = (
    "bajutsu/common/drivers/adb.py",
    "bajutsu/common/drivers/coordinate_tree.py",
    "bajutsu/common/drivers/playwright.py",
    "bajutsu/common/drivers/xcuitest.py",
    "bajutsu/common/drivers/xcuitest_live.py",
    "bajutsu/platform_lifecycle/environments/android.py",
    "bajutsu/platform_lifecycle/environments/web.py",
    "bajutsu/platform_lifecycle/environments/xcuitest.py",
    "bajutsu/platform_lifecycle/environments/xcuitest_live.py",
    "bajutsu/adb_resident.py",
    "bajutsu/provision.py",
    "bajutsu/serve/",
    "bajutsu/templates/",
    "bajutsu/cli/commands/record.py",
)


def _sweep_lookahead(paths: Iterable[str]) -> str:
    """A negative-lookahead body matching each ``bajutsu/``-relative path — a ``/``-terminated entry
    as a directory prefix, a file anchored with ``$`` — for the shared ``bajutsu/(?!…)`` sweep."""
    fragments = []
    for path in paths:
        rel = path.removeprefix("bajutsu/")
        fragments.append(re.escape(rel) if rel.endswith("/") else re.escape(rel) + r"$")
    return "|".join(fragments)


# The full set carved out of the shared `bajutsu/` sweep: the classified periphery plus the
# per-backend leaves the lanes reclaim. Derived from the two lists above so the regex cannot drift
# from them.
_SWEEP_EXCLUSIONS: tuple[str, ...] = (
    tuple(path for path, _ in _PERIPHERY_EXCLUSIONS) + _LANE_CLAIMED
)

_RUN_PATH = (
    # Sweep the whole shared core, carving out only the classified periphery and the per-backend
    # leaves each lane claims below (BE-0333). A new file under `bajutsu/` fires all three lanes until
    # it is classified here — the safe direction.
    r"bajutsu/(?!" + _sweep_lookahead(_SWEEP_EXCLUSIONS) + r")"
    r"|tests/driver_conformance\.py$"
    # The fault-injection scaffolding (BE-0305) two lanes' fault-injection jobs share, so a change to
    # it belongs in the shared core beside the conformance harness above, not in one lane fragment.
    r"|tests/fault_injection\.py$"
    # The onboarding-gate assertion each lane's `doctor` step runs (BE-0304); a change to it must
    # re-run every lane that exercises it, so it lives in the shared core, not one lane fragment.
    r"|scripts/assert_doctor_env\.py$"
    # This module itself. A change to the gate that decides which lanes run must run every lane, or
    # the new decision ships without a single lane ever having exercised it — the same silent
    # under-trigger the sweep guards against, one level up. Such a change is rare, so firing all three
    # lanes on it is cheap next to shipping the gate unvalidated.
    r"|scripts/e2e_changes\.py$"
    r"|pyproject\.toml$"
    r"|uv\.lock$"
)

# Each lane re-adds the per-backend leaves `_LANE_CLAIMED` carved out of the shared sweep — only the
# driver module(s), lifecycle environment(s), and (for iOS / web) the `record` CLI command its own
# backend imports — plus the non-`bajutsu/` surface it owns: its showcase apps, scenarios, conformance
# harness module, and workflow file. `bajutsu codegen` is on the shared sweep now (every lane relays
# it), so no lane names it; `record` is claimed here because the Android lane does not run it.
#
# The per-backend leaves default to over-firing: anything the shared sweep does not carve out —
# including a newly added driver or environment module — fires all three, and a lane fragment only
# narrows a known leaf back to its own backend. An earlier revision allow-listed each driver by name,
# which meant a new `common/drivers/<foo>.py` fired nothing and silently under-triggered every required check.
_LANE_PATHS: dict[str, str] = {
    "ios": (
        r"|bajutsu/common/drivers/(?:xcuitest|xcuitest_live)\.py$"
        # The XCUITest lifecycle environments (cold spawn, the warm resident lease, the BE-0292
        # bundled runner) — the iOS half of the `platform_lifecycle/` carve-out above.
        r"|bajutsu/platform_lifecycle/environments/(?:xcuitest|xcuitest_live)\.py$"
        r"|bajutsu/cli/commands/record\.py$"
        r"|tests/test_driver_conformance_ondevice\.py$"
        r"|tests/test_fault_injection_ondevice\.py$"
        # The pool-isolation assertion the lane's two-device job gates on (BE-0298). Claimed per lane
        # rather than swept into the shared core: only the iOS and Android lanes boot real devices, so
        # the web lane never invokes it and must not re-run its whole fleet when it changes.
        r"|scripts/assert_pool_isolation\.py$"
        r"|BajutsuKit/"
        r"|demos/showcase/ios/swiftui/"
        r"|demos/showcase/ios/uikit/"
        # The main config and the BE-0292 bundled-runner config the `bundled-runner (xcuitest)` job runs.
        r"|demos/showcase/showcase(?:\.[^/]+)?\.config\.yaml$"
        r"|demos/showcase/scenarios/"
        # The deterministic check the `network (xcuitest)` job runs over the persisted network.json
        # (BE-0282). iOS-only: the web lane has its own copy under the `demos/web/` sweep below, and
        # no other lane invokes this one.
        r"|demos/showcase/network/"
        r"|Makefile$"
        # The showcase's own Makefile (`e2e-visual` and friends) — the top-level `Makefile$` above is
        # anchored to the repo root and doesn't reach this one, but the `visual` job depends on it.
        r"|demos/showcase/Makefile$"
        r"|\.github/workflows/ios-e2e\.yml$"
        # The bootstrap every macOS job on this lane starts with — select Xcode, install uv and
        # xcodegen, start the Simulator boot, resolve the Xcode version for the cache key. Break it
        # and the whole lane breaks, so a change to it has to fire the lane; without this entry it
        # fired nothing and the required aggregator reported green having run none of it.
        r"|\.github/actions/setup-ios-toolchain/"
        r"|\.github/actions/bajutsu-e2e/"
        r"|\.github/actions/boot-simulator/"
        # The BE-0361 diagnostics collector every Simulator-driving job now calls. It runs on the
        # same jobs the two actions above do, and a change to it (a probe that hangs, a collection
        # that fails a step) can take a lane down as surely as editing the workflow file.
        r"|\.github/actions/collect-ios-diagnostics/"
    ),
    "android": (
        # Only the adb driver and the Python side of the resident UI Automator channel (BE-0245) this
        # lane exercises. coordinate_tree.py is adb.py's own read/settle core (BE-0254) — a change to
        # it can change adb's runtime behavior even though adb.py itself is untouched.
        r"|bajutsu/common/drivers/adb\.py$"
        r"|bajutsu/common/drivers/coordinate_tree\.py$"
        r"|bajutsu/adb_resident\.py$"
        # The Android lifecycle environment (boot, install, the BE-0236 provision profile) — the
        # Android half of the `platform_lifecycle/` carve-out.
        r"|bajutsu/platform_lifecycle/environments/android\.py$"
        r"|demos/showcase/android/"
        r"|demos/showcase/scenarios/"
        r"|demos/showcase/showcase\.config\.yaml$"
        r"|BajutsuAndroid/"  # the app-side clipboard SDK the showcase APKs build in (BE-0233)
        r"|BajutsuAndroidUIAutomatorServer/"  # the resident server this lane builds + exercises (BE-0245)
        r"|tests/test_driver_conformance_ondevice_android\.py$"
        r"|tests/test_fault_injection_ondevice_android\.py$"
        # The pool-isolation assertion this lane's two-device job gates on — see the iOS fragment.
        r"|scripts/assert_pool_isolation\.py$"
        r"|\.github/workflows/android-e2e\.yml$"
        r"|\.github/actions/setup-android-toolchain/"
        # The lane's own diagnostics collection (BE-0367): the host-telemetry action every job
        # brackets its emulator step with, and the device-side sweep each job's `script:` invokes.
        # Both run in every KVM job, so a break in either is only visible on this lane.
        r"|\.github/actions/collect-android-diagnostics/"
        r"|scripts/collect_android_diagnostics\.sh$"
        # The script the lane's two-emulator job runs (BE-0298): it boots the second emulator and
        # invokes the isolation assertion, so only this lane can exercise a change to it.
        r"|scripts/android_pool_e2e\.sh$"
    ),
    "web": (
        r"|bajutsu/common/drivers/playwright\.py$"
        # The web lifecycle environment (browser launch, context teardown) — the web half of the
        # `platform_lifecycle/` carve-out.
        r"|bajutsu/platform_lifecycle/environments/web\.py$"
        # The real provisioner the `onboarding (doctor / provision)` job runs as `python -m
        # bajutsu.provision --backend web` (BE-0304) to install Chromium for real. Web-only: no other
        # lane invokes it, and the lanes never run `scripts/install.sh`, its other caller.
        r"|bajutsu/provision\.py$"
        r"|bajutsu/cli/commands/record\.py$"
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

# The parallel-run surface the concurrent-device `pool` job guards (BE-0298) — narrower than any
# lane's own fragment above, because that job boots two devices where every other job boots one. It
# holds the code that decides how a run splits across devices and where each worker's evidence lands:
# the runner package (the pool itself, the pipeline that fans scenarios out to it), the whole
# lifecycle package (a change to any device's bring-up or teardown changes what two concurrent leases
# contend over), the two evidence modules that name a scenario's own directory, this module and the
# assertion the job gates on, the Android lane's own job definition and its two-emulator script, the
# showcase Android Makefile holding the `e2e-pool` target that job runs, and the Android toolchain
# composite that mints its first device. Over-selects by directory, the same safe direction the sweep
# above takes.
_POOL_PATHS = (
    r"bajutsu/runner/"
    r"|bajutsu/platform_lifecycle/"
    # `_resolve_lanes`: the `--udid` comma list resolved into the pool and `--workers` capped to its
    # size. The split itself lives here, so a change to it belongs on this surface even though the
    # shared `bajutsu/` sweep above already makes the module lane-relevant.
    r"|bajutsu/cli/commands/run\.py$"
    r"|bajutsu/evidence/core\.py$"
    r"|bajutsu/evidence/sink\.py$"
    r"|scripts/assert_pool_isolation\.py$"
    r"|scripts/e2e_changes\.py$"
    r"|scripts/android_pool_e2e\.sh$"
    r"|\.github/workflows/android-e2e\.yml$"
    r"|\.github/actions/setup-android-toolchain/"
    r"|demos/showcase/android/Makefile$"
)

_POOL_RE = re.compile(r"^(?:" + _POOL_PATHS + r")")

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
# A job's `if:` reading the `pool` output — the mark of a two-device job, which names its scenarios
# without being keyed on them (BE-0298). Matched anywhere in the job's block, since the guard is a
# folded scalar spanning lines.
_POOL_GUARD_RE = re.compile(r"needs\.changes\.outputs\.pool")
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


def touches_pool(paths: Iterable[str], lane: str = DEFAULT_LANE) -> bool:
    """Whether ``lane``'s concurrent-device job should run for this change (BE-0298).

    A conjunction: the change must be relevant to the lane at all *and* touch the parallel-run
    surface. The conjunction is what keeps the Android two-device job from firing on another lane's
    workflow file, which ``_POOL_PATHS`` names without distinguishing. Only the Android lane has such
    a job: the iOS half was withdrawn because two booted Simulators exhaust the hosted macOS runner
    (BE-0298), and a web lane's parallel workers are ``BrowserContext`` lanes with no device to
    contend over (BE-0054) — so those two lanes emit the ``pool`` output and leave it unread.

    Raises:
        ValueError: ``lane`` is unknown — see ``_lane_re``.
    """
    return is_relevant(paths, lane) and any(_POOL_RE.match(path) for path in paths)


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


def _is_pool_guard(line: str) -> bool:
    """Whether this line reads the `pool` output as part of a job's `if:` guard (BE-0298).

    A comment never counts. The scanner reads the comment block above a job key while `current_job`
    still names the *previous* job, so one prose line quoting the expression — which these workflows'
    comments do routinely — would drop that job from the map with no ``ValueError`` and no whole-fleet
    fallback: the single outcome `job_scenario_map` refuses. Every other pattern in this scanner is
    anchored or comment-aware; this keeps that true of the guard too.
    """
    return not line.lstrip().startswith("#") and _POOL_GUARD_RE.search(line) is not None


def job_scenario_map(workflow_text: str) -> dict[str, set[str]]:
    """Map each job to the scenario files it declares, read from a lane's workflow file (BE-0322).

    Reads the ``scenarios:`` inputs already present in the workflow, so the map is a lookup over the
    workflow's own declarations rather than a second list to maintain — it cannot drift from what
    each job runs. A job that declares no scenario — a dimension job (codegen / conformance /
    visual), or a lane with no scenario-keyed jobs at all — is simply absent, so an empty map is
    valid. A line scan keeps the caller on the standard library (the ``changes`` job runs a bare
    ``python3`` with no PyYAML); the format is regular block YAML pinned by the tests.

    A job guarded on ``needs.changes.outputs.pool`` is dropped even when it declares scenarios
    (BE-0298). The two-device jobs name the scenarios they run, but they are not *keyed* on them:
    editing one of those files must not fire a job that boots two devices, and the job's own ``if:``
    reads ``pool`` rather than ``affected``, so leaving it in the map would credit it with an
    ``affected`` entry no guard ever consults — which the guard/map equality test would then read as
    a rename that lost its guard.

    Raises:
        ValueError: the text has no ``jobs`` block; a ``scenarios:`` value is a quoted path, a list,
            or a literal block scalar (``|``); or a path in a folded block scalar (``>``/``>-``) is
            not a plain unquoted path. The caller falls back to firing the whole lane rather than
            trusting a mis-parsed map that would narrow the lane wrongly.
    """
    result: dict[str, set[str]] = {}
    pool_keyed: set[str] = set()
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
        elif current_job is not None and _is_pool_guard(line):
            pool_keyed.add(current_job)
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
    for job in pool_keyed:
        result.pop(job, None)
    return result


# These jobs declare their scenarios in `demos/showcase/Makefile` targets, not in a workflow
# `scenarios:` input (BE-0338). `codegen` codegens and runs the `ui-test` + `ui-test-coverage`
# targets; `visual` runs the `e2e-visual` pixel VRT; `network` runs the `e2e-network` capture / mock /
# redaction lane (BE-0282). Their attribution is read from those recipes — the one place the scenarios
# are named — so it cannot be a second copy that a Makefile edit outdates.
# `test_makefile_declared_scenarios_match_the_targets` pins the extracted set, so a target gaining or
# losing a scenario fails `make check` unless the attribution moves with it, the same no-drift
# invariant BE-0322's action-input map holds (`conformance` stays a dimension job: it drives the whole
# harness and declares no scenario subset, so it is deliberately absent here).
_MAKEFILE_JOB_TARGETS: dict[str, tuple[str, ...]] = {
    "codegen": ("ui-test", "ui-test-coverage"),
    "visual": ("e2e-visual",),
    "network": ("e2e-network",),
}

# A `demos/showcase/scenarios/…` YAML path as it appears literally in a Makefile recipe — the same
# repo-root-relative form `changed_files` and `_LANE_SCENARIO_RE` compare against.
_MAKEFILE_SCENARIO_RE = re.compile(r"demos/showcase/scenarios/[\w./-]+\.ya?ml")


def affected_jobs(changed_scenarios: Iterable[str], job_map: dict[str, set[str]]) -> set[str]:
    """The jobs whose declared scenarios intersect the changed scenario files.

    A scenario reused across jobs (``smoke.yaml``, declared by both ``run`` and ``bundled-runner``)
    selects every job that declares it, because each one exercises it.
    """
    changed = set(changed_scenarios)
    return {job for job, scenarios in job_map.items() if scenarios & changed}


def _makefile_target_scenarios(makefile_text: str, target: str) -> set[str]:
    """The scenario files a Makefile target's recipe names (BE-0338).

    Scans the tab-indented recipe lines following the target header and collects every
    ``demos/showcase/scenarios/…yaml`` path they reference. Blank and comment lines inside the recipe
    are skipped; the first column-0 line that is neither blank nor a comment ends the recipe — so a
    prefix-sharing target (``ui-test`` vs ``ui-test-coverage``) or a comment between recipes never
    bleeds one target's scenarios into another's.
    """
    header_re = re.compile(rf"^{re.escape(target)}\s*:(?!=)")
    scenarios: set[str] = set()
    in_recipe = False
    for line in makefile_text.splitlines():
        if in_recipe:
            if line.startswith("\t"):
                # A tab-then-`#` line is a shell comment `make` never runs, so it names no scenario
                # the target exercises — skip it, the same as a column-0 comment.
                if not line.lstrip().startswith("#"):
                    scenarios.update(_MAKEFILE_SCENARIO_RE.findall(line))
                continue
            if not line.strip() or line.lstrip().startswith("#"):
                continue  # blank / comment within or just after the recipe
            in_recipe = False  # a real column-0 line ends the recipe
        if header_re.match(line):
            in_recipe = True
    return scenarios


def makefile_job_scenarios(makefile_text: str) -> dict[str, set[str]]:
    """Map each Makefile-declared job to the scenarios its showcase target runs (BE-0338).

    Raises:
        ValueError: a mapped job lists no target at all, or any mapped target names no scenario — a
            recipe rename or a parse failure. The scenario check is per target, not per job: a job
            whose second target still yields scenarios would otherwise hide a first target that
            silently went empty, dropping that target's scenarios from the job's set and
            under-attributing it. Failing on the empty target instead makes the caller fall back to
            firing the whole lane, the same fail-closed direction ``job_scenario_map`` takes.
    """
    result: dict[str, set[str]] = {}
    for job, targets in _MAKEFILE_JOB_TARGETS.items():
        # The one degenerate input the per-target ladder below cannot see: zero targets iterates zero
        # times, so the job would land in the map with an empty scenario set and then be selected by
        # nothing — a silent under-fire, the direction this module exists to avoid.
        if not targets:
            raise ValueError(f"job {job!r} lists no showcase Makefile target")
        scenarios: set[str] = set()
        for target in targets:
            target_scenarios = _makefile_target_scenarios(makefile_text, target)
            if not target_scenarios:
                raise ValueError(
                    f"showcase Makefile target {target!r} for job {job!r} names no scenarios"
                )
            scenarios |= target_scenarios
        result[job] = scenarios
    return result


def showcase_makefile_text() -> str | None:
    """The text of the showcase Makefile, or None when it is absent (BE-0338)."""
    path = _REPO_ROOT / "demos" / "showcase" / "Makefile"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def lane_workflow_text(lane: str) -> str | None:
    """The text of ``lane``'s E2E workflow file, or None when it is absent."""
    path = _REPO_ROOT / ".github" / "workflows" / f"{lane}-e2e.yml"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def lane_job_scenario_map(lane: str, workflow_text: str) -> dict[str, set[str]]:
    """The full job-to-scenario map for ``lane`` (BE-0322 + BE-0338).

    The workflow's own ``scenarios:`` declarations (``job_scenario_map``), plus — on the iOS lane
    only — the scenarios the showcase Makefile declares for ``codegen`` / ``visual`` / ``network``,
    folded in so a change to one of them names its job in ``affected``. Android and web key no jobs on
    scenarios, so their map is the workflow map unchanged.

    Raises:
        ValueError: the workflow YAML, or (on iOS) the showcase Makefile, can't be parsed. The caller
            falls back to firing the whole lane.
    """
    job_map = job_scenario_map(workflow_text)
    if lane == DEFAULT_LANE:
        makefile_text = showcase_makefile_text()
        if makefile_text is None:
            raise ValueError(
                "showcase Makefile is absent; cannot key its Makefile-declared jobs on scenarios"
            )
        for job, scenarios in makefile_job_scenarios(makefile_text).items():
            job_map.setdefault(job, set()).update(scenarios)
    return job_map


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
        job_map = lane_job_scenario_map(lane, text)
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


def _emit(relevant: bool, shared: bool, affected: list[str], pool: bool) -> None:
    """Print the verdict and append it to ``GITHUB_OUTPUT`` when the workflow provides one.

    Emits the three outputs the lane's jobs read (BE-0322): ``relevant`` (run any metered job at
    all), ``shared`` (a shared-code change — fire the whole lane), and ``affected`` (a JSON array of
    the scenario-keyed jobs a scenario-only change reached; empty unless the change was narrowed),
    plus ``pool`` (the change reached the parallel-run surface, so the two-device job runs — BE-0298).
    """
    lines = [
        f"relevant={str(relevant).lower()}",
        f"shared={str(shared).lower()}",
        f"affected={json.dumps(affected)}",
        f"pool={str(pool).lower()}",
    ]
    for line in lines:
        print(line)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main() -> int:
    lane = os.environ.get("E2E_LANE", DEFAULT_LANE)
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("HEAD_SHA", "")
    if not base:
        # workflow_dispatch: no PR context, so nothing to path-gate against — run the whole lane,
        # the two-device job included (a manual dispatch is how that job is exercised on demand).
        _emit(relevant=True, shared=True, affected=[], pool=True)
        return 0

    changed = changed_files(base, head)
    print(f"Lane: {lane}")
    print("Changed files:")
    for path in changed:
        print(f"  {path}")

    kind = classify_change(changed, lane)
    if kind == "none":
        _emit(relevant=False, shared=False, affected=[], pool=False)
        return 0

    # A scenario-only change narrows to the jobs that declare a changed scenario; anything else — a
    # shared-code change, or a scenario-only change the workflow can't attribute (`None`) — fires the
    # whole lane, the safe over-selection.
    affected = _affected_or_fallback(changed, lane) if kind == "scenario-only" else None
    _emit(
        relevant=True,
        shared=affected is None,
        affected=affected or [],
        pool=touches_pool(changed, lane),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
