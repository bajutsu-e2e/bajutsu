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
  a serve-only PR. A new subpackage, top-level module, or CLI command defaults to NOT triggering —
  add its pattern to ``_RUN_PATH`` (all lanes) or the lane's own fragment.

Invoked by each workflow with ``BASE_SHA`` / ``HEAD_SHA`` in the environment and ``E2E_LANE`` naming
the lane (``ios`` — the default — / ``android`` / ``web``); it writes ``relevant=true|false`` to
``GITHUB_OUTPUT``. An empty ``BASE_SHA`` (a manual ``workflow_dispatch`` with no PR context) always
counts as relevant.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable

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
_RUN_PATH = (
    r"bajutsu/(?:runner|scenario|orchestrator|codegen)/"
    r"|bajutsu/(?:"
    r"_yaml|adb|artifact_perms|backends|capabilities|capability_preflight"
    r"|config|config_source|device_id|dom|dotenv|elements"
    r"|handoff|idb_version|interp|mailbox|platform_lifecycle|record"
    r"|run_id|screenshots|simctl|totp|web_network|webview"
    r")\.py$"
    r"|bajutsu/crawl/(?:core|serialize|__init__)\.py$"
    r"|bajutsu/agents/(?:protocols|__init__)\.py$"
    r"|bajutsu/evidence/(?:core|intervals|network|visual|golden|redaction|__init__)\.py$"
    r"|bajutsu/assertions/"
    # The driver abstraction (Point/Element/Selector, the Driver Protocol, selector resolution) every
    # backend's driver module imports, along with the shared run-path modules above (runner/,
    # orchestrator/, …) — universal, so it lives in the shared core rather than any one lane fragment.
    r"|bajutsu/drivers/base\.py$"
    r"|bajutsu/cli/__init__\.py$"
    r"|bajutsu/cli/_shared\.py$"
    r"|bajutsu/cli/commands/__init__\.py$"
    r"|bajutsu/cli/commands/run\.py$"
    r"|tests/driver_conformance\.py$"
    r"|pyproject\.toml$"
    r"|uv\.lock$"
)

# Each lane adds its own driver, app, scenarios, conformance harness, and workflow file on top of
# `_RUN_PATH`. The lane differences are real: iOS and web codegen/record scenarios (so their CLI
# commands are relevant) while the Android lane runs only `bajutsu run`; iOS and web exercise every
# driver while Android touches only `drivers/adb.py` (+ the resident channel); each lane owns its
# showcase surface, its conformance harness module, and its own workflow file.
_LANE_PATHS: dict[str, str] = {
    "ios": (
        r"|bajutsu/drivers/"
        r"|bajutsu/cli/commands/(?:codegen|record)\.py$"
        r"|tests/test_driver_conformance_ondevice\.py$"
        r"|BajutsuKit/"
        r"|demos/showcase/ios/swiftui/"
        r"|demos/showcase/ios/uikit/"
        # The main config and the BE-0292 bundled-runner config the `xcuitest (multi-touch)` job runs.
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
        # Only the adb driver (the top-level list does not reach into subdirectories) and the Python
        # side of the resident UI Automator channel (BE-0245) this lane exercises. coordinate_tree.py
        # is the shared read/settle core adb.py and idb.py both subclass (BE-0254) — relevant here
        # too, not only to iOS's full `bajutsu/drivers/` sweep.
        r"|bajutsu/drivers/adb\.py$"
        r"|bajutsu/drivers/coordinate_tree\.py$"
        r"|bajutsu/adb_resident\.py$"
        r"|demos/showcase/android/"
        r"|demos/showcase/scenarios/"
        r"|demos/showcase/showcase\.config\.yaml$"
        r"|BajutsuAndroid/"  # the app-side clipboard SDK the showcase APKs build in (BE-0233)
        r"|BajutsuAndroidUIAutomatorServer/"  # the resident server this lane builds + exercises (BE-0245)
        r"|tests/test_driver_conformance_ondevice_android\.py$"
        r"|\.github/workflows/android-e2e\.yml$"
    ),
    "web": (
        r"|bajutsu/drivers/"
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


def is_relevant(paths: Iterable[str], lane: str = DEFAULT_LANE) -> bool:
    """Whether any changed path is one the given lane's E2E jobs actually exercise.

    Raises:
        ValueError: ``lane`` is none of the known lanes. ``E2E_LANE`` is a literal each workflow
            hard-codes, not user input, so an unrecognized value is a config bug — it must fail the
            `changes` job loudly rather than silently substitute another lane's filter, which could
            under-trigger and let a required aggregator report green without exercising this lane.
    """
    try:
        pattern = _LANE_RE[lane]
    except KeyError:
        raise ValueError(f"Unknown E2E lane {lane!r}; expected one of {sorted(_LANE_RE)}") from None
    return any(pattern.match(p) for p in paths)


def changed_files(base: str, head: str) -> list[str]:
    """The PR's own changed files, via a merge-base (three-dot) diff of ``base`` and ``head``."""
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def _emit(relevant: bool) -> None:
    """Print the verdict and append it to ``GITHUB_OUTPUT`` when the workflow provides one."""
    line = f"relevant={'true' if relevant else 'false'}"
    print(line)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def main() -> int:
    lane = os.environ.get("E2E_LANE", DEFAULT_LANE)
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("HEAD_SHA", "")
    if not base:
        # workflow_dispatch: no PR context, so nothing to path-gate against — always run.
        _emit(True)
        return 0

    changed = changed_files(base, head)
    print(f"Lane: {lane}")
    print("Changed files:")
    for path in changed:
        print(f"  {path}")
    _emit(is_relevant(changed, lane))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
