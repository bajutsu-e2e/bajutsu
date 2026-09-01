"""Tests for scripts/e2e_changes.py — the per-lane E2E relevance filter (each lane's `changes` job).

The three E2E lanes (ios / android / web) each carry a required aggregator, so none can be path-gated
at the trigger; instead this filter decides, per lane, whether the heavy jobs run. These tests pin the
pieces: the shared run-path core and each lane's own surface (`is_relevant`, keyed by lane), and — the
regression this script exists for — that `changed_files` uses a merge-base (three-dot) diff, so a PR
whose base branch has moved on isn't charged for files it never touched.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

from scripts.e2e_changes import (
    _LANE_CLAIMED,
    _LANE_PATHS,
    _LANE_SCENARIO_PATHS,
    _MAKEFILE_JOB_TARGETS,
    _PERIPHERY_EXCLUSIONS,
    _POOL_PATHS,
    _RUN_PATH,
    _makefile_target_scenarios,
    affected_jobs,
    changed_files,
    classify_change,
    is_relevant,
    job_scenario_map,
    lane_job_scenario_map,
    lane_workflow_text,
    main,
    makefile_job_scenarios,
    showcase_makefile_text,
    touches_pool,
)


def test_roadmap_only_change_is_not_relevant() -> None:
    paths = [
        "roadmaps/proposals/BE-XXXX-foo/BE-XXXX-foo.md",
        "roadmaps/proposals/BE-XXXX-foo/BE-XXXX-foo-ja.md",
        "roadmaps/README.md",
    ]
    assert is_relevant(paths) is False


def test_empty_diff_is_not_relevant() -> None:
    assert is_relevant([]) is False


def test_run_path_subpackage_is_relevant() -> None:
    assert is_relevant(["bajutsu/runner/pipeline.py"]) is True


def test_run_path_top_level_modules_are_relevant() -> None:
    # The run / codegen / record surface: the run loop, assertions, the element model, the driver
    # helpers, the visual/golden dimensions, codegen, the run-pipeline's direct dependencies
    # (evidence, redaction, artifact_perms, mailbox), and record.py's direct imports (the agent
    # protocols, crawl core, handoff). Under the BE-0333 inverted default these fire because the
    # shared core sweeps `bajutsu/` and none of them is a classified periphery exclusion.
    for module in (
        "bajutsu/interp.py",
        # assertions is a package (BE-0250); the whole package is on the run path, so every
        # module under it triggers — not just a single-file `assertions.py`.
        "bajutsu/assertions/__init__.py",
        "bajutsu/assertions/evaluate.py",
        "bajutsu/assertions/visual.py",
        "bajutsu/assertions/network.py",
        "bajutsu/assertions/schema.py",
        "bajutsu/assertions/_common.py",
        "bajutsu/elements.py",
        "bajutsu/evidence/visual.py",
        "bajutsu/evidence/golden.py",
        "bajutsu/codegen/emit.py",
        "bajutsu/record.py",
        "bajutsu/adb.py",
        "bajutsu/simctl.py",
        # runner/pipeline.py and orchestrator/loop.py unconditional imports
        "bajutsu/evidence/core.py",
        # `bajutsu.evidence.core` executes `evidence/__init__.py` on import, same as
        # `crawl/__init__.py` / `agents/__init__.py` below.
        "bajutsu/evidence/__init__.py",
        "bajutsu/evidence/redaction.py",
        "bajutsu/evidence/network.py",
        "bajutsu/artifact_perms.py",
        "bajutsu/mailbox.py",
        "bajutsu/evidence/intervals.py",
        # record.py unconditionally imports the Agent/EnrichmentAgent protocols from
        # agents.protocols (record is an E2E verb), mirroring the old agent.py entry (now
        # agent_protocols.py, packaged by BE-0257). Its sibling agents.factory (the old
        # agents.py / agent_factory.py) is deliberately excluded — see the parity test below.
        "bajutsu/agents/protocols.py",
        # `bajutsu.agents.protocols` executes `agents/__init__.py` on import, same as
        # `crawl/__init__.py` below.
        "bajutsu/agents/__init__.py",
        "bajutsu/crawl/core.py",
        # record imports `screen_identity` through the package re-export, so `__init__` is on the
        # on-device import path — and `__init__` unconditionally imports `serialize` too, putting it
        # on that path as well (the periphery siblings are not — see the parity test below).
        "bajutsu/crawl/__init__.py",
        "bajutsu/crawl/serialize.py",
        "bajutsu/handoff.py",
    ):
        assert is_relevant([module]) is True, module


def test_non_run_path_top_level_modules_are_not_relevant() -> None:
    # A serve/analytics/crawl module lives at the top level too, and the on-device jobs never import
    # it, so touching it must not burn the metered macOS jobs. Under the BE-0333 inverted default each
    # of these fires nothing only because it is a classified `_PERIPHERY_EXCLUSIONS` entry — a new,
    # unclassified module would fire (that is the point). (PR #936, a serve-only change, wrongly fired
    # all four before the periphery was carved out.)
    for module in (
        "bajutsu/analysis/stats.py",
        "bajutsu/analysis/audit.py",
        "bajutsu/analysis/coverage.py",
        "bajutsu/common/analytics/stats.py",
        "bajutsu/agents/alerts.py",
        "bajutsu/common/github/actions.py",
        # The crawl engine core/serialize/__init__ trigger (above), but the periphery siblings in the
        # same package do not — the on-device run never imports them, so `crawl/**` must not be swept
        # wholesale. All five are pinned so the boundary is fully covered, not just sampled.
        "bajutsu/crawl/guide.py",
        "bajutsu/crawl/report.py",
        "bajutsu/crawl/repro.py",
        "bajutsu/crawl/flows.py",
        "bajutsu/crawl/tabs.py",
    ):
        assert is_relevant([module]) is False, module


def test_report_package_is_relevant_on_every_lane() -> None:
    # The headline miss BE-0333 fixes: `bajutsu/report/` holds the manifest writer every run invokes
    # through `runner/pipeline.py`, yet the old positive list never swept the package, so a change to
    # it fired no lane and the required aggregators reported green without exercising it. The inverted
    # default sweeps the whole package in; the HTML renderer no lane asserts on rides along as a
    # cheap over-fire rather than being carved out file by file.
    for lane in ("ios", "android", "web"):
        for path in (
            "bajutsu/report/manifest.py",
            "bajutsu/report/__init__.py",
            "bajutsu/report/html.py",
        ):
            assert is_relevant([path], lane) is True, (lane, path)
    # ...but the MCP server stays a classified periphery subpackage the E2E never exercises.
    assert is_relevant(["bajutsu/mcp/server.py"]) is False


def test_previously_missed_run_path_modules_now_fire() -> None:
    # The BE-0333 motivation cites these among the run-path-closure files that fired no lane under the
    # old positive list. Each is a top-level module the run imports (through the report writer, the
    # deprecation shims, the object store, record capture, or the grouping helper), so a change to it
    # must re-run the lanes; the inverted default sweeps each in.
    for module in (
        "bajutsu/deprecations.py",
        "bajutsu/object_store.py",
        "bajutsu/record_capture.py",
        "bajutsu/from_grouping.py",
    ):
        assert is_relevant([module]) is True, module


def test_new_unclassified_module_fires_every_lane() -> None:
    # The property BE-0333 buys: a module added anywhere under `bajutsu/` that nobody has classified
    # fires all three lanes (a wasted job, the safe direction) rather than defaulting to firing
    # nothing and surfacing later as a mysteriously green required check. A top-level module, a file
    # in a swept subpackage, and a brand-new CLI command all over-fire until classified.
    for path in (
        "bajutsu/brand_new_module.py",
        "bajutsu/runner/brand_new.py",
        "bajutsu/cli/commands/brand_new.py",
    ):
        for lane in ("ios", "android", "web"):
            assert is_relevant([path], lane) is True, (lane, path)


def test_only_listed_cli_commands_are_relevant() -> None:
    assert is_relevant(["bajutsu/cli/commands/run.py"]) is True
    assert is_relevant(["bajutsu/cli/commands/trace.py"]) is False


def test_conformance_suite_is_relevant_but_other_tests_are_not() -> None:
    # The on-device conformance suite (BE-0114) runs in these jobs, so a change to its contract or
    # its harness must re-run them; an ordinary unit test the E2E never executes must not.
    assert is_relevant(["tests/driver_conformance.py"]) is True
    assert is_relevant(["tests/test_driver_conformance_ondevice.py"]) is True
    assert is_relevant(["tests/test_e2e_changes.py"]) is False


def test_the_ios_toolchain_composite_fires_its_own_lane() -> None:
    # Every macOS job on the iOS lane starts with this composite (Xcode, uv, xcodegen, the Simulator
    # boot), so breaking it breaks the lane — yet it matched no fragment until BE-0298, which meant a
    # change to it fired nothing and the required `E2E (iOS)` aggregator reported green having run
    # none of it. Its Android counterpart was always claimed; this pins the symmetry.
    ios = ".github/actions/setup-ios-toolchain/action.yml"
    android = ".github/actions/setup-android-toolchain/action.yml"
    assert is_relevant([ios], "ios") is True
    assert is_relevant([ios], "android") is False
    assert is_relevant([android], "android") is True
    assert is_relevant([android], "ios") is False


def test_only_e2e_workflow_is_relevant() -> None:
    assert is_relevant([".github/workflows/ios-e2e.yml"]) is True
    assert is_relevant([".github/workflows/ci.yml"]) is False


def test_showcase_makefile_is_relevant_but_root_makefile_path_form_matters() -> None:
    # The showcase's own Makefile drives the `visual` job's `e2e-visual` target; the root
    # `Makefile$` alternative doesn't reach into subdirectories, so it needs its own entry.
    assert is_relevant(["demos/showcase/Makefile"]) is True


def test_any_relevant_path_amid_irrelevant_ones_triggers() -> None:
    assert is_relevant(["roadmaps/README.md", "docs/foo.md", "BajutsuKit/Sources/x.swift"]) is True


# --- Per-lane filters (android / web) ------------------------------------------------------------
# Each lane shares the run-path core and adds its own driver, app, scenarios, conformance harness, and
# workflow file. These pin the shared core across lanes and each lane's own surface, including the
# exclusions that keep a required check from firing on an unrelated change.


def test_shared_run_path_is_relevant_on_every_lane() -> None:
    # The run / codegen / record importable surface (`_RUN_PATH`) is identical across lanes, so a
    # change to it re-runs all three. Sample the subpackage sweep, a top-level allow-listed module,
    # the assertions package, and the shared deps.
    for lane in ("ios", "android", "web"):
        assert is_relevant(["bajutsu/runner/pipeline.py"], lane) is True, lane
        assert is_relevant(["bajutsu/interp.py"], lane) is True, lane
        assert is_relevant(["bajutsu/assertions/evaluate.py"], lane) is True, lane
        assert is_relevant(["tests/driver_conformance.py"], lane) is True, lane
        # The onboarding-gate assertion every lane's `doctor` step runs (BE-0304).
        assert is_relevant(["scripts/assert_doctor_env.py"], lane) is True, lane
        # This filter itself: a change to the gate deciding which lanes run must run every lane,
        # or the new decision ships without a lane ever exercising it. Its own test file must not —
        # a unit test the lanes never execute is the `test_e2e_changes.py` exclusion pinned below.
        assert is_relevant(["scripts/e2e_changes.py"], lane) is True, lane
        assert is_relevant(["tests/test_e2e_changes.py"], lane) is False, lane
        assert is_relevant(["uv.lock"], lane) is True, lane


def test_doctor_onboarding_gate_code_is_relevant_on_every_lane() -> None:
    # All three lanes run `bajutsu doctor --environment-only` as the BE-0304 onboarding gate and
    # assert its output with `scripts/assert_doctor_env.py`. The assertion script was allow-listed
    # from the start; the code it asserts against was not, so a change to the doctor gate itself
    # skipped every lane that exercises it — the gate could regress with all three lanes green.
    for lane in ("ios", "android", "web"):
        for path in (
            "bajutsu/doctor.py",
            "bajutsu/cli/commands/doctor.py",
            "bajutsu/preflight.py",
            # `preflight.py` builds every `environment:` check's tool list and remedy strings from
            # this, so a change here moves the section the gate asserts on too.
            "bajutsu/requirements.py",
            # The platform-neutral DeviceError base (BE-0260) that both `run` and doctor catch.
            "bajutsu/device_errors.py",
        ):
            assert is_relevant([path], lane) is True, (lane, path)


def test_doctors_ai_availability_half_stays_excluded() -> None:
    # `cli/commands/doctor.py` also reports whether AI credentials are present, via
    # `agents/availability` and the `credential_gap` lookup `bajutsu/ai/registry.py` backs. Those
    # stay excluded on purpose: `assert_doctor_env.py` reads only the `environment:` section, which no
    # AI credential can move, and sweeping them in would drag the whole AI periphery onto the metered
    # lanes.
    for lane in ("ios", "android", "web"):
        assert is_relevant(["bajutsu/agents/availability.py"], lane) is False, lane
        assert is_relevant(["bajutsu/ai/registry.py"], lane) is False, lane
        assert is_relevant(["bajutsu/ai/__init__.py"], lane) is False, lane


def test_provision_is_web_only() -> None:
    # The web lane's `onboarding (doctor / provision)` job runs `python -m bajutsu.provision
    # --backend web` to install Chromium for real (BE-0304), so a provisioner change is web-relevant.
    # No other lane invokes it: neither runs `scripts/install.sh`, its only other caller.
    assert is_relevant(["bajutsu/provision.py"], "web") is True
    assert is_relevant(["bajutsu/provision.py"], "ios") is False
    assert is_relevant(["bajutsu/provision.py"], "android") is False


def test_serve_analytics_modules_are_relevant_on_no_lane_except_web_serve() -> None:
    # The serve/analytics/crawl-periphery modules the E2E never imports must not fire any lane —
    # except that the web lane *does* exercise the serve backend (the serve-UI dogfood), so
    # `bajutsu/serve/**` is web-relevant while `bajutsu/analysis/stats.py` (analytics) is relevant
    # to none.
    for lane in ("ios", "android", "web"):
        assert is_relevant(["bajutsu/analysis/stats.py"], lane) is False, lane
        assert is_relevant(["bajutsu/crawl/report.py"], lane) is False, lane
    assert is_relevant(["bajutsu/serve/app.py"], "web") is True
    assert is_relevant(["bajutsu/serve/app.py"], "android") is False
    assert is_relevant(["bajutsu/serve/app.py"], "ios") is False


def test_ios_lane_surface() -> None:
    # iOS (the default lane) drives only the XCUITest driver modules, BajutsuKit, its own showcase
    # apps, its own conformance harness, and its own workflow file.
    assert is_relevant(["bajutsu/drivers/xcuitest.py"]) is True
    assert is_relevant(["bajutsu/drivers/xcuitest_live.py"]) is True
    assert is_relevant(["BajutsuKit/Sources/x.swift"]) is True
    assert is_relevant(["demos/showcase/ios/swiftui/App.swift"]) is True
    assert is_relevant(["tests/test_driver_conformance_ondevice.py"]) is True
    assert is_relevant([".github/workflows/ios-e2e.yml"]) is True
    # The composite actions every Simulator-driving job calls: the run wrapper, the boot, and the
    # BE-0361 diagnostics collector. A change to any of them can take a lane down as surely as
    # editing the workflow file, so each must fire the lane that runs it.
    assert is_relevant([".github/actions/bajutsu-e2e/action.yml"]) is True
    assert is_relevant([".github/actions/boot-simulator/action.yml"]) is True
    assert is_relevant([".github/actions/collect-ios-diagnostics/action.yml"]) is True
    # The deterministic check the `network (xcuitest)` job runs over the persisted network.json
    # (BE-0282). iOS-only: no other lane invokes it, and it must fire *this* lane — otherwise editing
    # the checker fires nothing, the job never runs on the PR that changed it, and `E2E (iOS)` reports
    # green having tested none of it.
    assert is_relevant(["demos/showcase/network/assert_network_evidence.py"]) is True
    assert is_relevant(["demos/showcase/network/assert_network_evidence.py"], "android") is False
    assert is_relevant(["demos/showcase/network/assert_network_evidence.py"], "web") is False
    # ...but not another lane's driver, app SDK, or workflow — the regression this fixes: a bare
    # `bajutsu/drivers/` sweep previously fired the metered macOS jobs on an adb-only or
    # playwright-only change that XCUITest never imports.
    assert is_relevant(["bajutsu/drivers/adb.py"]) is False
    assert is_relevant(["bajutsu/drivers/coordinate_tree.py"]) is False
    assert is_relevant(["bajutsu/drivers/playwright.py"]) is False
    assert is_relevant(["BajutsuAndroid/src/Clipboard.kt"]) is False
    assert is_relevant([".github/workflows/web-e2e.yml"]) is False


def test_android_lane_surface() -> None:
    # Android drives only the adb driver (+ the resident channel), its own showcase and app SDKs, its
    # own conformance harness, and its own workflow file.
    assert is_relevant(["bajutsu/drivers/adb.py"], "android") is True
    assert is_relevant(["bajutsu/adb_resident.py"], "android") is True
    assert is_relevant(["demos/showcase/android/Makefile"], "android") is True
    assert is_relevant(["BajutsuAndroid/src/Clipboard.kt"], "android") is True
    assert is_relevant(["BajutsuAndroidUIAutomatorServer/src/Server.kt"], "android") is True
    assert is_relevant(["tests/test_driver_conformance_ondevice_android.py"], "android") is True
    assert is_relevant([".github/workflows/android-e2e.yml"], "android") is True
    # The composite action every job in the workflow calls for its Gradle/JDK/cache setup — a change
    # there (e.g. the `cache-read-only` default, the pinned `setup-gradle` SHA) can change every job's
    # behavior as much as editing the workflow file itself.
    assert is_relevant([".github/actions/setup-android-toolchain/action.yml"], "android") is True
    # The BE-0367 diagnostics collection every KVM job runs: the host-telemetry action bracketing
    # each emulator step, and the device-side sweep each job's `script:` invokes. A break in either
    # is only visible on this lane, so a pattern dropped from the fragment would go unseen.
    assert (
        is_relevant([".github/actions/collect-android-diagnostics/action.yml"], "android") is True
    )
    assert is_relevant(["scripts/collect_android_diagnostics.sh"], "android") is True
    # The `uiautomator (codegen)` job (BE-0294) regenerates its test with `bajutsu codegen`, so the
    # codegen CLI command is android-relevant — the one CLI command besides `run` this lane drives.
    assert is_relevant(["bajutsu/cli/commands/codegen.py"], "android") is True
    # ...but not another lane's driver, app, or workflow.
    assert is_relevant(["bajutsu/drivers/playwright.py"], "android") is False
    assert is_relevant(["BajutsuKit/Sources/x.swift"], "android") is False
    assert is_relevant([".github/workflows/web-e2e.yml"], "android") is False


def test_android_lane_catches_the_adb_drivers_own_dependencies() -> None:
    # adb.py imports `bajutsu.drivers.base` (the Driver Protocol / selector resolution every driver
    # subclasses) and `bajutsu.drivers.coordinate_tree` (the read/settle core, BE-0254) — a change
    # to either can change adb's runtime behavior, so both must trigger the
    # Android lane even though its fragment narrows the rest of `bajutsu/drivers/` to `adb.py` alone.
    assert is_relevant(["bajutsu/drivers/base.py"], "android") is True
    assert is_relevant(["bajutsu/drivers/coordinate_tree.py"], "android") is True
    # base.py is universal — every lane's driver imports it, so it triggers on every lane too.
    for lane in ("ios", "android", "web"):
        assert is_relevant(["bajutsu/drivers/base.py"], lane) is True, lane


def test_web_lane_surface() -> None:
    # The web lane drives only the Playwright driver, the serve backend + templates (the serve-UI
    # dogfood), the web + serve-ui demos, its own conformance harness, and its own workflow file.
    assert is_relevant(["bajutsu/drivers/playwright.py"], "web") is True
    assert is_relevant(["bajutsu/serve/app.py"], "web") is True
    assert is_relevant(["bajutsu/templates/report.html"], "web") is True
    assert is_relevant(["demos/serve-ui/scenario.yaml"], "web") is True
    assert is_relevant(["demos/web/scenario.yaml"], "web") is True
    assert is_relevant(["tests/test_driver_conformance_web.py"], "web") is True
    assert is_relevant([".github/workflows/web-e2e.yml"], "web") is True
    # ...but not the Android app SDK, the iOS showcase, another lane's driver, or another lane's
    # workflow — the regression this fixes: a bare `bajutsu/drivers/` sweep previously fired the
    # Playwright jobs on an XCUITest-only or adb-only change that `playwright.py` never imports.
    assert is_relevant(["BajutsuAndroid/src/Clipboard.kt"], "web") is False
    assert is_relevant(["demos/showcase/ios/swiftui/App.swift"], "web") is False
    assert is_relevant([".github/workflows/android-e2e.yml"], "web") is False
    assert is_relevant([".github/actions/setup-android-toolchain/action.yml"], "web") is False
    assert is_relevant(["bajutsu/drivers/xcuitest.py"], "web") is False
    assert is_relevant(["bajutsu/drivers/xcuitest_live.py"], "web") is False
    assert is_relevant(["bajutsu/drivers/adb.py"], "web") is False
    assert is_relevant(["bajutsu/drivers/coordinate_tree.py"], "web") is False


# --- Package sweeps that a module→package split used to silently break ----------------------------
# The historical failure: a top-level module the old positive list named with a trailing `\.py$` was
# split into a package (`config`, BE-0252; `platform_lifecycle`), and every file under it stopped
# matching, so the lane's `changes` job reported `relevant=false` and the required aggregator went
# green without running a thing — `platform_lifecycle/environments/xcuitest.py` owns the XCUITest
# cold spawn the iOS lane exists to exercise, and PR #1403 changed it to a fully skipped fleet. The
# BE-0333 inverted default retires that whole class: the shared core sweeps `bajutsu/` wholesale, so
# a package and a module match alike and a split cannot drop a file out of the filter. These pin that
# the once-bitten packages fire.


def test_lifecycle_package_files_are_relevant() -> None:
    # The regression: `platform_lifecycle` became a package, so every one of these matched nothing.
    # The package root, the shared plumbing, and the environments the whole family shares fire every
    # lane; the per-backend leaves are pinned to their own lane below.
    for lane in ("ios", "android", "web"):
        for path in (
            "bajutsu/platform_lifecycle/__init__.py",
            "bajutsu/platform_lifecycle/factories.py",
            "bajutsu/platform_lifecycle/protocols.py",
            "bajutsu/platform_lifecycle/readiness.py",
            "bajutsu/platform_lifecycle/device_control.py",
            "bajutsu/platform_lifecycle/read_session.py",
            "bajutsu/platform_lifecycle/relaunchers.py",
            "bajutsu/platform_lifecycle/environments/__init__.py",
            "bajutsu/platform_lifecycle/environments/ios.py",
            "bajutsu/platform_lifecycle/environments/_bundled_runner.py",
            "bajutsu/platform_lifecycle/environments/fake.py",
        ):
            assert is_relevant([path], lane) is True, (lane, path)


def test_lifecycle_environment_leaves_fire_only_their_own_lane() -> None:
    # The four per-backend `Environment` leaves follow the `bajutsu/drivers/` contract one layer up:
    # an XCUITest-only lifecycle change must not burn the Android KVM or web Playwright jobs, which
    # never import it. Sweeping the package without this carve-out would trade the under-trigger for
    # an over-trigger on `environments/xcuitest.py`, the most-churned file in the package.
    owner = {
        "bajutsu/platform_lifecycle/environments/xcuitest.py": "ios",
        "bajutsu/platform_lifecycle/environments/xcuitest_live.py": "ios",
        "bajutsu/platform_lifecycle/environments/android.py": "android",
        "bajutsu/platform_lifecycle/environments/web.py": "web",
    }
    for path, own_lane in owner.items():
        for lane in ("ios", "android", "web"):
            assert is_relevant([path], lane) is (lane == own_lane), (lane, path)


def test_config_package_files_are_relevant() -> None:
    # The same drift, one release earlier: BE-0252 split `config.py` into a package, and config
    # resolution feeds every lane's run, so all three must fire.
    for lane in ("ios", "android", "web"):
        for path in (
            "bajutsu/config/__init__.py",
            "bajutsu/config/resolve.py",
            "bajutsu/config/effective.py",
            "bajutsu/config/schema.py",
            "bajutsu/config/accessors.py",
        ):
            assert is_relevant([path], lane) is True, (lane, path)
    # `config_source.py` is a separate top-level module and still matches by name — the swept
    # `bajutsu/config/` prefix must not be what carries it.
    assert is_relevant(["bajutsu/config_source.py"]) is True


# --- Structural guards against a silent under-trigger ---------------------------------------------
# The two guards below do not check any single path; they check the shape of the filter, so a future
# edit cannot reintroduce the class of bug that motivated them. The first pins the "no orphan" rule
# for the two per-backend directories. The second catches a renamed or deleted path, which the repo
# has been bitten by before: a moved workflow or demo leaves a pattern matching nothing, and the lane
# stops firing exactly as silently as the package split did.

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories split per backend: a file is either claimed by the lanes whose backend imports it, or
# swept into the shared core. Neither may leave a file matching nothing on every lane.
_PER_BACKEND_DIRS = ("bajutsu/drivers", "bajutsu/platform_lifecycle/environments")


def test_no_per_backend_file_is_orphaned() -> None:
    # Every file under a per-backend directory must fire at least one lane. A file that fires none is
    # invisible to CI: it can break any lane that imports it while all three required checks stay
    # green. Both directories sweep by default and exclude only the leaves a lane claims, so a newly
    # added module fires all three until a lane narrows it — the safe direction.
    for rel in _PER_BACKEND_DIRS:
        directory = _REPO_ROOT / rel
        assert directory.is_dir(), f"{rel} moved; update _PER_BACKEND_DIRS"
        for path in sorted(directory.glob("*.py")):
            target = f"{rel}/{path.name}"
            lanes = [lane for lane in ("ios", "android", "web") if is_relevant([target], lane)]
            assert lanes, f"{target} fires no lane — it would break CI silently"


# Every leaf `_LANE_CLAIMED` carves out of the shared sweep must be re-claimed by at least one lane
# fragment. The two lists are maintained independently — the shared exclusion (this constant, via
# `_SWEEP_EXCLUSIONS`) and each lane's `_LANE_PATHS` fragment — so a leaf carved out but claimed by no
# lane fires *nothing*: the silent under-trigger the whole item guards against. (The opposite drift, a
# leaf claimed by a lane but not carved out, would fire all three; the per-lane surface tests above
# pin each claimed leaf to exactly its owning lane, catching that.)
def test_lane_claimed_leaves_fire_at_least_one_lane() -> None:
    for path in _LANE_CLAIMED:
        # A directory entry (serve/ , templates/) stands in for the files under it; probe a
        # representative path so `is_relevant`'s prefix match has something to bite on.
        probe = f"{path}app.py" if path.endswith("/") else path
        lanes = [lane for lane in ("ios", "android", "web") if is_relevant([probe], lane)]
        assert lanes, (
            f"{path} is carved out of the shared sweep but claimed by no lane — it fires nothing"
        )


def test_lane_claimed_leaves_are_not_swept_into_every_lane() -> None:
    # The carve-out's reason for being: a claimed leaf must fire *fewer* than all three lanes (or the
    # exclusion is pointless and it should just be swept). A driver/environment leaf fires one lane; the
    # broadest, `record`, fires two (iOS + web, not Android). None may fire all three.
    for path in _LANE_CLAIMED:
        probe = f"{path}app.py" if path.endswith("/") else path
        lanes = [lane for lane in ("ios", "android", "web") if is_relevant([probe], lane)]
        assert len(lanes) < 3, (
            f"{path} fires every lane — carving it out of the shared sweep buys nothing"
        )


# --- The classified periphery (BE-0333 Unit 1 / Unit 3) ------------------------------------------


def test_periphery_exclusions_fire_no_lane() -> None:
    # Every `_PERIPHERY_EXCLUSIONS` entry is a deliberate "the E2E never runs this" decision, so none
    # of them may fire any lane. A directory entry stands in for its files; probe a representative path.
    for path, reason in _PERIPHERY_EXCLUSIONS:
        probe = f"{path}probe.py" if path.endswith("/") else path
        for lane in ("ios", "android", "web"):
            assert is_relevant([probe], lane) is False, (lane, path, reason)


def test_periphery_exclusion_paths_exist() -> None:
    # A renamed or deleted periphery path leaves a stale exclusion carving nothing out, so a run-path
    # file that later takes its place would be swept in silently mislabeled. Check each against the
    # tree, the same drift guard `test_every_plain_literal_path_in_the_filter_exists` applies to the
    # positive patterns.
    for path, _reason in _PERIPHERY_EXCLUSIONS:
        target = _REPO_ROOT / path
        if path.endswith("/"):
            assert target.is_dir(), f"periphery exclusion {path!r} is not a directory on disk"
        else:
            assert target.is_file(), f"periphery exclusion {path!r} is not a file on disk"


def test_periphery_exclusions_carry_a_reason() -> None:
    # The point of moving the ad-hoc parity tests (BE-0333 Unit 3) into this list is that every
    # exclusion reads as a decision. An empty reason is a decision nobody wrote down.
    for path, reason in _PERIPHERY_EXCLUSIONS:
        assert reason.strip(), f"periphery exclusion {path!r} carries no reason"


# --- The run-path import-closure cross-check (BE-0333 Unit 2) -------------------------------------
# The inverted default (Unit 1) makes a *new* file over-fire, but on its own it cannot see the reverse
# error: a module the run path *starts* importing that is not classified — swept in silently, or worse,
# reached while sitting on the periphery list. This walks the run path's static import closure with
# `ast` (parse, never import — the `changes` job runs a bare `python3` with no dependencies, and this
# belongs in the fast `make check` gate) and fails if any file it reaches is neither gated by a lane
# nor a classified periphery entry. It is the mechanical enforcement of the item's title: every
# run-path file is either gated or explicitly excluded.

# The files the three lanes actually execute — `bajutsu run` / `codegen` / `record` / `doctor`, the
# web lane's `python -m bajutsu.provision`, and the on-device conformance harness. Their transitive
# `bajutsu` imports are the run path this check must keep fully classified.
_RUN_ENTRYPOINTS = (
    "bajutsu/cli/commands/run.py",
    "bajutsu/cli/commands/codegen.py",
    "bajutsu/cli/commands/record.py",
    "bajutsu/cli/commands/doctor.py",
    "bajutsu/provision.py",
    "tests/driver_conformance.py",
)


def _module_to_relpath(module: str) -> str | None:
    """`bajutsu.a.b` -> the repo-relative file path of its module or package `__init__`, or None."""
    rel = module.replace(".", "/")
    for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
        if (_REPO_ROOT / candidate).is_file():
            return candidate
    return None


def _containing_package(relpath: str) -> list[str]:
    """The package a module or package-init file lives in, as dotted components. Dropping the last
    component covers both: `bajutsu/agents/protocols.py` and `bajutsu/agents/__init__.py` alike sit in
    `bajutsu.agents`, so a `from . import x` in either resolves to `bajutsu.agents.x` (a package init
    that kept its `__init__` component would resolve to `bajutsu.agents.__init__.x` and drop the edge)."""
    return relpath.removesuffix(".py").replace("/", ".").split(".")[:-1]


def _imports_from_source(relpath: str, source: str) -> set[str]:
    """The absolute `bajutsu.*` modules `source` (the text of `relpath`) imports, resolving relative
    imports against `relpath`'s containing package."""
    tree = ast.parse(source, filename=relpath)
    package = _containing_package(relpath)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("bajutsu"))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                base = ".".join(
                    package[: len(package) - (node.level - 1)]
                    + ([node.module] if node.module else [])
                )
            if not base.startswith("bajutsu"):
                continue
            found.add(
                base
            )  # `from pkg import name` may pull a symbol; the package itself is imported
            found.update(f"{base}.{a.name}" for a in node.names if a.name != "*")
    return found


def _bajutsu_imports(relpath: str) -> set[str]:
    """The absolute `bajutsu.*` modules `relpath` imports, resolving relative imports to absolute."""
    return _imports_from_source(relpath, (_REPO_ROOT / relpath).read_text(encoding="utf-8"))


def test_containing_package_drops_module_and_init_alike() -> None:
    # The fix a package-init resolver depends on: both a module and its package `__init__` sit in the
    # same package, so a relative import in either resolves against that package — not against a
    # phantom `…__init__` component that would drop the edge from the closure walk.
    assert _containing_package("bajutsu/agents/protocols.py") == ["bajutsu", "agents"]
    assert _containing_package("bajutsu/agents/__init__.py") == ["bajutsu", "agents"]


def test_relative_imports_resolve_to_absolute() -> None:
    # The closure walk must follow `from . import x` / `from .mod import y` edges (the codebase has
    # none today, but Unit 2 exists to survive that drift). A `__init__.py` resolving a relative import
    # to `bajutsu.agents.__init__.factory` instead of `bajutsu.agents.factory` would silently drop the
    # run-path file behind it — the exact miss this check guards against.
    found = _imports_from_source(
        "bajutsu/agents/__init__.py",
        "from . import protocols\nfrom .factory import make_agent\nfrom ..runner import pipeline\n",
    )
    assert "bajutsu.agents.protocols" in found
    assert "bajutsu.agents.factory" in found
    assert "bajutsu.runner.pipeline" in found
    assert not any("__init__" in name for name in found), found


def _run_path_closure() -> set[str]:
    """Every `bajutsu/` file transitively imported by the run entrypoints (a static `ast` walk)."""
    seen = set(_RUN_ENTRYPOINTS)
    queue = list(_RUN_ENTRYPOINTS)
    while queue:
        for module in _bajutsu_imports(queue.pop()):
            relpath = _module_to_relpath(module)
            if relpath is not None and relpath.startswith("bajutsu/") and relpath not in seen:
                seen.add(relpath)
                queue.append(relpath)
    return {p for p in seen if p.startswith("bajutsu/")}


def _covered_by_periphery(relpath: str) -> bool:
    return any(
        relpath.startswith(path) if path.endswith("/") else relpath == path
        for path, _reason in _PERIPHERY_EXCLUSIONS
    )


def test_run_path_closure_is_gated_or_excluded() -> None:
    # The Unit 2 invariant. Walk the run path's import closure and require every file in it to be
    # either gated (fires some lane) or a classified periphery entry. A run-path module that is
    # neither — a per-backend leaf carved out of the shared sweep but re-claimed by no lane, or a
    # periphery module the run path newly imports that nobody has added to `_PERIPHERY_EXCLUSIONS` —
    # fails here, at `make check`, instead of surfacing months later as a mysteriously green required
    # check. `record` reaching `agents/factory` (the agent factory) is the canonical acknowledged
    # crossing: it is covered because `factory.py` is a periphery entry, not because it is gated.
    closure = _run_path_closure()
    assert len(closure) > 100, (
        f"closure walk reached only {len(closure)} files — did the entrypoints move?"
    )
    uncovered = sorted(
        relpath
        for relpath in closure
        if not any(is_relevant([relpath], lane) for lane in ("ios", "android", "web"))
        and not _covered_by_periphery(relpath)
    )
    assert not uncovered, (
        "run-path files neither gated by a lane nor in `_PERIPHERY_EXCLUSIONS`:\n"
        + "\n".join(f"  {p}" for p in uncovered)
        + "\nGate each (it is on the run path) or add it to `_PERIPHERY_EXCLUSIONS` with a reason."
    )


def _plain_literal_paths(pattern: str) -> list[str]:
    """The unambiguous filesystem paths in one alternation, skipping genuinely regex branches.

    Splits on the top-level ``|`` only (a ``|`` nested inside ``(?:…)`` or ``(?!…)`` separates
    alternatives *within* one branch, not branches), then keeps the branches that are plain paths once
    ``\\.`` is unescaped. A branch still holding regex syntax — ``Makefile$`` has none but
    ``showcase(?:\\.[^/]+)?\\.config\\.yaml$`` does — is not a single path and is skipped.
    """
    branches, depth, current = [], 0, ""
    for char in pattern:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "|" and depth == 0:
            branches.append(current)
            current = ""
            continue
        current += char
    branches.append(current)

    plain = []
    for branch in branches:
        candidate = branch.removesuffix("$").replace("\\.", ".")
        if not candidate or any(ch in candidate for ch in "()?[]*+!\\"):
            continue  # a real regex branch, not one path
        plain.append(candidate)
    return plain


def test_every_plain_literal_path_in_the_filter_exists() -> None:
    # A renamed or deleted path leaves its pattern matching nothing, and the lane stops firing with no
    # signal at all — the same failure mode as a module becoming a package, reached by a different
    # route. Checking the patterns against the real tree turns that into a `make check` failure on the
    # PR doing the rename.
    checked = 0
    for label, pattern in [("shared", _RUN_PATH), ("pool", _POOL_PATHS), *_LANE_PATHS.items()]:
        for candidate in _plain_literal_paths(pattern.removeprefix("|")):
            target = _REPO_ROOT / candidate
            if candidate.endswith("/"):
                assert target.is_dir(), f"{label}: directory {candidate!r} does not exist"
            else:
                assert target.exists(), f"{label}: path {candidate!r} does not exist"
            checked += 1
    # A floor, so a rewrite that leaves the extraction matching nothing fails here rather than
    # passing vacuously. Raise it freely; it only records that the check still has teeth.
    assert checked >= 30, f"only {checked} literal paths checked — has the filter's shape changed?"


def test_unrecognized_lane_raises_instead_of_silently_substituting() -> None:
    # E2E_LANE is a literal each workflow hard-codes, not user input, so a typo (e.g. "andorid") is a
    # config bug that must fail the `changes` job loudly. Silently substituting another lane's filter
    # is not a safe fallback: no lane is a superset of another (iOS lacks BajutsuAndroid/, adb_resident,
    # bajutsu/serve/, …), so a mistyped lane could under-trigger and let a required aggregator report
    # green without ever running that lane's jobs — the very failure mode this guards against.
    with pytest.raises(ValueError, match="bogus"):
        is_relevant(["BajutsuKit/Sources/x.swift"], "bogus")


def test_main_respects_the_e2e_lane_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # main() reads E2E_LANE and applies that lane's filter end to end: an Android-only change is
    # relevant to the android lane but not the web lane over the same diff.
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")
    main_tip = _commit(tmp_path, "bajutsu/runner/pipeline.py", "unrelated on main")
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "BajutsuAndroid/src/Clipboard.kt", "android app SDK only")

    for lane, expected in (
        ("android", "relevant=true\nshared=true\naffected=[]\npool=false\n"),
        ("web", "relevant=false\nshared=false\naffected=[]\npool=false\n"),
    ):
        output = tmp_path / f"github_output_{lane}"
        monkeypatch.setenv("E2E_LANE", lane)
        monkeypatch.setenv("BASE_SHA", main_tip)
        monkeypatch.setenv("HEAD_SHA", pr_tip)
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        assert main() == 0
        assert output.read_text(encoding="utf-8") == expected, lane


def test_main_raises_on_a_misconfigured_e2e_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A mistyped E2E_LANE in a workflow file must fail the `changes` job step (a visibly red job),
    # not exit 0 having silently applied the wrong lane's filter end to end through main().
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")
    main_tip = _commit(tmp_path, "bajutsu/runner/pipeline.py", "unrelated on main")
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "roadmaps/proposals/BE-XXXX-foo/BE-XXXX-foo.md", "roadmap only")

    monkeypatch.setenv("E2E_LANE", "andorid")
    monkeypatch.setenv("BASE_SHA", main_tip)
    monkeypatch.setenv("HEAD_SHA", pr_tip)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github_output"))
    with pytest.raises(ValueError, match="andorid"):
        main()


def _git(tmp_path: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _init_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A pre-push hook (make check) exports GIT_DIR / GIT_INDEX_FILE into this process; left set they
    # redirect the nested git calls at the outer repo. Clear them, then init an isolated repo with a
    # throwaway identity so `git commit` works on a bare CI runner too.
    for var in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    for key, value in (
        ("user.email", "t@example.com"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        _git(tmp_path, "config", key, value)


def _commit(tmp_path: Path, rel: str, message: str) -> str:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", message)
    return _git(tmp_path, "rev-parse", "HEAD")


def test_changed_files_uses_merge_base_not_branch_tips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bug this fixes: `base` is the base-branch tip, and when it has advanced past the PR's fork
    # point a two-dot `git diff base head` reports every file main touched meanwhile — so an
    # unrelated bajutsu/runner change on main would trip the filter on a roadmap-only PR. A
    # three-dot (merge-base) diff yields only the PR's own changes.
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")

    # main advances with an on-device-relevant file the PR never touches.
    main_tip = _commit(tmp_path, "bajutsu/runner/pipeline.py", "unrelated run-path change on main")

    # The PR branch, forked before that, changes only a roadmap file.
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "roadmaps/proposals/BE-XXXX-foo/BE-XXXX-foo.md", "roadmap only")

    changed = changed_files(main_tip, pr_tip)
    assert changed == ["roadmaps/proposals/BE-XXXX-foo/BE-XXXX-foo.md"]
    assert is_relevant(changed) is False


def test_main_workflow_dispatch_is_always_relevant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No PR context (a manual workflow_dispatch): with no base to diff against, main() emits
    # relevant=true to GITHUB_OUTPUT without touching git. Pins the contract the docstring states
    # and the workflow's `changes` job relies on.
    monkeypatch.delenv("BASE_SHA", raising=False)
    monkeypatch.delenv("HEAD_SHA", raising=False)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert main() == 0
    assert (
        output.read_text(encoding="utf-8") == "relevant=true\nshared=true\naffected=[]\npool=true\n"
    )


def test_main_emits_false_for_a_roadmap_only_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # main() end to end over the base-advanced scenario: it reads BASE_SHA/HEAD_SHA, runs the
    # merge-base diff, and writes relevant=false to GITHUB_OUTPUT for a roadmap-only PR.
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")
    main_tip = _commit(tmp_path, "bajutsu/runner/pipeline.py", "unrelated on main")
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "roadmaps/proposals/BE-XXXX-foo/BE-XXXX-foo.md", "roadmap only")

    output = tmp_path / "github_output"
    monkeypatch.setenv("BASE_SHA", main_tip)
    monkeypatch.setenv("HEAD_SHA", pr_tip)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert main() == 0
    assert (
        output.read_text(encoding="utf-8")
        == "relevant=false\nshared=false\naffected=[]\npool=false\n"
    )


# --- The concurrent-device pool filter (BE-0298) --------------------------------------------------
# The Android two-emulator job boots twice what every other job on its lane boots, and nothing else on
# any lane can observe what it checks. So it is keyed on a narrower surface than the lane-wide signal:
# `touches_pool` is that surface, conjoined with the lane's own relevance. It is the only such job
# left — the iOS half was withdrawn, since two booted Simulators exhaust the hosted macOS runner — so
# the iOS and web lanes emit the output and leave it unread.


def test_pool_fires_on_the_parallel_run_surface() -> None:
    for path in (
        "bajutsu/runner/pool.py",
        "bajutsu/runner/pipeline.py",
        "bajutsu/platform_lifecycle/environments/android.py",
        # `_resolve_lanes` — the comma `--udid` list turned into the pool, and the `--workers` cap.
        "bajutsu/cli/commands/run.py",
        "bajutsu/evidence/core.py",
        "bajutsu/evidence/sink.py",
        "scripts/assert_pool_isolation.py",
        "scripts/android_pool_e2e.sh",
        # Mints the first emulator; the script boots the second beside it.
        ".github/actions/setup-android-toolchain/action.yml",
        "demos/showcase/android/Makefile",
    ):
        assert touches_pool([path], "android") is True, path


def test_pool_does_not_fire_on_a_relevant_change_off_that_surface() -> None:
    # The whole point of the narrower key: an ordinary driver or scenario change fires the lane's
    # single-device jobs (`is_relevant` stays true) without paying for two booted emulators.
    for path in (
        "bajutsu/drivers/adb.py",
        "demos/showcase/scenarios/smoke.yaml",
        "demos/showcase/android/compose/build.gradle.kts",
    ):
        assert is_relevant([path], "android") is True, path
        assert touches_pool([path], "android") is False, path


def test_pool_is_conjoined_with_the_lanes_own_relevance() -> None:
    # `_POOL_PATHS` names the Android lane's two-emulator machinery without naming the lane, so the
    # conjunction with `is_relevant` is what stops another lane's change firing that job.
    assert touches_pool([".github/workflows/android-e2e.yml"], "android") is True
    assert touches_pool([".github/workflows/android-e2e.yml"], "ios") is False
    assert touches_pool(["scripts/android_pool_e2e.sh"], "android") is True
    assert touches_pool(["scripts/android_pool_e2e.sh"], "ios") is False
    assert touches_pool(["demos/showcase/android/Makefile"], "android") is True
    assert touches_pool(["demos/showcase/android/Makefile"], "ios") is False


def test_the_ios_lane_has_no_pool_keyed_job_left() -> None:
    # BE-0298's iOS half is withdrawn, so the iOS lane's own workflow and its Simulator bring-up leave
    # the pool surface with it: no job reads that lane's `pool` output any more, so those paths would
    # only describe a job that no longer exists. They could not have fired the Android job either way —
    # each sits in `_LANE_PATHS["ios"]` alone, and `touches_pool` conjoins the surface with the lane's
    # own relevance.
    assert touches_pool([".github/workflows/ios-e2e.yml"], "ios") is False
    assert touches_pool([".github/actions/boot-simulator/action.yml"], "ios") is False
    assert touches_pool([".github/actions/bajutsu-e2e/action.yml"], "ios") is False
    text = lane_workflow_text("ios")
    assert text is not None
    assert "outputs.pool" not in text


def test_a_pool_keyed_job_is_dropped_even_where_it_declares_scenarios() -> None:
    workflow = """\
jobs:
  run:
    if: needs.changes.outputs.relevant == 'true'
    steps:
      - uses: ./.github/actions/bajutsu-e2e
        with:
          scenarios: demos/showcase/scenarios/smoke.yaml
  pool:
    if: >-
      needs.changes.outputs.relevant == 'true' &&
      needs.changes.outputs.pool == 'true'
    steps:
      - uses: ./.github/actions/bajutsu-e2e
        with:
          scenarios: demos/showcase/scenarios/smoke.yaml
"""
    assert job_scenario_map(workflow) == {"run": {"demos/showcase/scenarios/smoke.yaml"}}


def test_a_comment_quoting_the_pool_guard_does_not_drop_the_previous_job() -> None:
    # The scanner reads the comment block above a job key while `current_job` still names the job
    # before it, so an unanchored guard match on prose would drop that job from the map with no
    # ValueError and no whole-fleet fallback — the one outcome `job_scenario_map` refuses. These
    # workflows' comments quote expressions verbatim, so the case is not hypothetical.
    workflow = """\
jobs:
  run:
    if: needs.changes.outputs.relevant == 'true'
    steps:
      - uses: ./.github/actions/bajutsu-e2e
        with:
          scenarios: demos/showcase/scenarios/smoke.yaml
  # Keyed on needs.changes.outputs.pool rather than the lane-wide signal, so an ordinary change
  # does not pay for two booted devices.
  pool:
    if: needs.changes.outputs.pool == 'true'
    steps:
      - uses: ./.github/actions/bajutsu-e2e
        with:
          scenarios: demos/showcase/scenarios/smoke.yaml
"""
    assert job_scenario_map(workflow) == {"run": {"demos/showcase/scenarios/smoke.yaml"}}


def test_pool_does_not_fire_on_an_irrelevant_change() -> None:
    assert touches_pool(["roadmaps/README.md", "docs/architecture.md"]) is False
    assert touches_pool([]) is False


def test_pool_raises_on_an_unknown_lane() -> None:
    # Same reasoning as `is_relevant`: E2E_LANE is a hard-coded literal, so a typo must fail the
    # `changes` job rather than silently substitute another lane's filter.
    with pytest.raises(ValueError, match="Unknown E2E lane"):
        touches_pool(["bajutsu/runner/pool.py"], "andorid")


# --- Change classification (BE-0322) -------------------------------------------------------------
# The scenario-scoped narrowing rests on a three-way partition of a lane's changed files: `none`
# (nothing the lane exercises — skip it, as today), `scenario-only` (every relevant path is a
# scenario file, so the affected jobs can be narrowed), and `shared` (a relevant path outside the
# scenario files — shared driver / runner / app / workflow code that can affect any scenario, so the
# whole lane fires). `classify_change` is that partition over the paths `is_relevant` already
# recognizes.


def test_classify_change_none_when_no_relevant_path() -> None:
    # A roadmap-only PR touches nothing the lane exercises, so there is nothing to narrow — the lane
    # skips wholesale, exactly as `is_relevant` returning False does today.
    assert classify_change(["roadmaps/README.md", "docs/foo.md"]) == "none"
    assert classify_change([]) == "none"


def test_classify_change_scenario_only_for_scenario_files() -> None:
    # A change confined to scenario files is the one case the narrowing can prove safe.
    assert classify_change(["demos/showcase/scenarios/smoke.yaml"]) == "scenario-only"
    assert (
        classify_change(
            ["demos/showcase/scenarios/smoke.yaml", "demos/showcase/scenarios/golden/golden.yaml"]
        )
        == "scenario-only"
    )


def test_classify_change_ignores_irrelevant_paths_when_partitioning() -> None:
    # An irrelevant path riding along (a doc, a roadmap file) is not a scenario file, but it is also
    # not something the lane exercises — so it must not tip a scenario-only change into `shared`.
    assert (
        classify_change(["demos/showcase/scenarios/smoke.yaml", "docs/foo.md", "README.md"])
        == "scenario-only"
    )


def test_classify_change_shared_when_a_relevant_non_scenario_path_changes() -> None:
    # Shared driver / runner / app / workflow code can affect any scenario, so any relevant path
    # outside the scenario files fires the whole lane — even alongside a scenario-only edit.
    assert classify_change(["bajutsu/runner/pipeline.py"]) == "shared"
    assert classify_change(["bajutsu/drivers/xcuitest.py"]) == "shared"
    assert classify_change([".github/workflows/ios-e2e.yml"]) == "shared"
    assert (
        classify_change(["demos/showcase/scenarios/smoke.yaml", "bajutsu/runner/pipeline.py"])
        == "shared"
    )


def test_classify_change_is_lane_specific() -> None:
    # The scenario surface is per lane: the showcase scenarios are the iOS and Android scenario
    # files, while the web lane's scenarios live under its own demos. A path that is a scenario file
    # on one lane may be shared (or irrelevant) on another.
    assert classify_change(["demos/showcase/scenarios/smoke.yaml"], "android") == "scenario-only"
    assert classify_change(["demos/web/scenario.yaml"], "web") == "scenario-only"
    # The web lane's serve backend is relevant but not a scenario file — a shared change there.
    assert classify_change(["bajutsu/serve/app.py"], "web") == "shared"


def test_classify_change_raises_on_unknown_lane() -> None:
    with pytest.raises(ValueError, match="bogus"):
        classify_change(["demos/showcase/scenarios/smoke.yaml"], "bogus")


# --- Job-to-scenario map and affected-job selection (BE-0322) ------------------------------------
# The map is read straight from the workflow's own `scenarios:` declarations, so it can't drift from
# what each job actually runs. `affected_jobs` intersects a change's scenario files against it.

_WORKFLOW_SAMPLE = """
jobs:
  run:
    steps:
      - uses: ./.github/actions/setup-ios-toolchain
      - uses: ./.github/actions/bajutsu-e2e
        with:
          scenarios: demos/showcase/scenarios/smoke.yaml
          target: showcase-swiftui
  actuation:
    steps:
      - uses: ./.github/actions/bajutsu-e2e
        with:
          scenarios: demos/showcase/scenarios/navigation.yaml
      - uses: ./.github/actions/bajutsu-e2e
        with:
          scenarios: demos/showcase/scenarios/device.yaml
  bundled-runner:
    steps:
      - uses: ./.github/actions/bajutsu-e2e
        with:
          scenarios: demos/showcase/scenarios/smoke.yaml
  conformance:
    steps:
      - run: uv run pytest tests/test_driver_conformance_ondevice.py -m ondevice
"""


def test_job_scenario_map_reads_each_jobs_declared_scenarios() -> None:
    job_map = job_scenario_map(_WORKFLOW_SAMPLE)
    assert job_map == {
        "run": {"demos/showcase/scenarios/smoke.yaml"},
        "actuation": {
            "demos/showcase/scenarios/navigation.yaml",
            "demos/showcase/scenarios/device.yaml",
        },
        "bundled-runner": {"demos/showcase/scenarios/smoke.yaml"},
    }
    # A dimension job that declares no scenario (only a `run:` step) is absent from the map.
    assert "conformance" not in job_map


def test_job_scenario_map_raises_on_a_malformed_workflow() -> None:
    # A workflow that isn't the expected `jobs:` mapping is a config bug; the caller falls back to
    # the whole fleet on it rather than trusting a silently empty map (see the main() fallback test).
    with pytest.raises(ValueError, match="jobs"):
        job_scenario_map("- just\n- a\n- list\n")


def test_job_scenario_map_rejects_a_non_plain_scenario_value() -> None:
    # A quoted value or an unsupported block scalar (`|` literal) raises rather than mis-parsing —
    # the caller falls back to the whole fleet rather than silently under-firing a job.
    for value in ('"demos/showcase/scenarios/smoke.yaml"', "|"):
        workflow = (
            "jobs:\n"
            "  run:\n"
            "    steps:\n"
            "      - uses: ./.github/actions/bajutsu-e2e\n"
            "        with:\n"
            f"          scenarios: {value}\n"
        )
        with pytest.raises(ValueError, match="scenarios"):
            job_scenario_map(workflow)


def test_job_scenario_map_handles_folded_block_scalar() -> None:
    # Workflows that consolidate multiple scenarios in one step use a folded block scalar (`>-`).
    # The scanner must collect each path from the continuation lines rather than raise ValueError.
    workflow = (
        "jobs:\n"
        "  actuation:\n"
        "    steps:\n"
        "      - uses: ./.github/actions/bajutsu-e2e\n"
        "        with:\n"
        "          scenarios: >-\n"
        "            demos/showcase/scenarios/navigation.yaml\n"
        "            demos/showcase/scenarios/device.yaml\n"
        "          target: showcase-swiftui\n"
    )
    assert job_scenario_map(workflow) == {
        "actuation": {
            "demos/showcase/scenarios/navigation.yaml",
            "demos/showcase/scenarios/device.yaml",
        }
    }


def test_job_scenario_map_is_not_truncated_by_a_column_zero_comment() -> None:
    # A column-0 comment between two jobs must not end the jobs-block scan: dropping the jobs below it
    # would silently under-fire (e.g. skip `bundled-runner`, which also declares smoke.yaml). Only a
    # real top-level key ends the block.
    workflow = (
        "jobs:\n"
        "  run:\n"
        "    steps:\n"
        "      - uses: ./.github/actions/bajutsu-e2e\n"
        "        with:\n"
        "          scenarios: demos/showcase/scenarios/smoke.yaml\n"
        "# a section comment at column 0\n"
        "  bundled-runner:\n"
        "    steps:\n"
        "      - uses: ./.github/actions/bajutsu-e2e\n"
        "        with:\n"
        "          scenarios: demos/showcase/scenarios/smoke.yaml\n"
    )
    assert set(job_scenario_map(workflow)) == {"run", "bundled-runner"}


def test_every_scenario_path_prefix_is_also_relevant() -> None:
    # The module comment states the load-bearing invariant: each _LANE_SCENARIO_PATHS prefix is a
    # subset of that lane's _LANE_PATHS fragment "so a scenario file is always relevant first". If a
    # new scenario prefix were added here without a matching relevance fragment, classify_change would
    # return `none` for a real scenario edit and the whole required lane would skip silently (the
    # exact failure mode guarded elsewhere). Pin it: a representative path under each prefix must be
    # relevant.
    for lane, pattern in _LANE_SCENARIO_PATHS.items():
        for prefix in pattern.split("|"):
            representative = prefix + "any.yaml"
            assert is_relevant([representative], lane), (
                f"Scenario prefix {prefix!r} in _LANE_SCENARIO_PATHS[{lane!r}] is not covered by "
                f"is_relevant — {representative!r} is not relevant for lane {lane!r}. "
                f"Add the prefix to _LANE_PATHS[{lane!r}] to restore the subset invariant."
            )


def test_every_scenario_keyed_job_guard_matches_the_map() -> None:
    # Each scenario-keyed iOS job carries an `if:` guard `contains(fromJSON(...affected...), '<job>')`.
    # That literal must equal the job id the map is keyed on: a rename that updates the job key but not
    # its guard (or vice versa) would leave a job whose guard never fires on a scenario-only change.
    # Pin the guarded-job set equal to the map-key set so such a divergence fails here. The map is the
    # combined lane map (BE-0338): `codegen` and `visual` declare their scenarios in the showcase
    # Makefile, so they are keyed via `lane_job_scenario_map`, not `job_scenario_map` alone.
    text = lane_workflow_text("ios")
    assert text is not None
    guarded = set(
        re.findall(r"contains\(fromJSON\(needs\.changes\.outputs\.affected\), '([^']+)'\)", text)
    )
    assert guarded == set(lane_job_scenario_map("ios", text))


def test_affected_jobs_intersects_changed_scenarios_with_the_map() -> None:
    job_map = job_scenario_map(_WORKFLOW_SAMPLE)
    # A change to navigation.yaml reaches only the job that declares it.
    assert affected_jobs(["demos/showcase/scenarios/navigation.yaml"], job_map) == {"actuation"}
    # A scenario declared by two jobs (smoke.yaml) fires both, because each exercises it.
    assert affected_jobs(["demos/showcase/scenarios/smoke.yaml"], job_map) == {
        "run",
        "bundled-runner",
    }
    # A scenario no job declares reaches nothing (the caller turns this into a whole-fleet fallback).
    assert affected_jobs(["demos/showcase/scenarios/nonexistent.yaml"], job_map) == set()


def test_real_ios_workflow_declares_the_scenario_keyed_jobs() -> None:
    # Pin the map against the real workflow: the scenario-keyed jobs the narrowing acts on, and
    # smoke.yaml declared by both `run` and `bundled-runner`. A job rename or a scenario move surfaces
    # here rather than silently mis-narrowing the lane.
    text = lane_workflow_text("ios")
    assert text is not None
    job_map = job_scenario_map(text)
    assert set(job_map) == {"run", "actuation", "golden", "bundled-runner"}
    assert "demos/showcase/scenarios/smoke.yaml" in job_map["run"]
    assert job_map["bundled-runner"] == {"demos/showcase/scenarios/smoke.yaml"}
    # The golden job runs both tree goldens on one warm runner: golden.yaml's Stable rows, and
    # golden_xcuitest.yaml's Log-tab controls — whose second scenario asserts the same baseline with
    # `run --touch-markers`'s in-app markers on, so the visualization cannot perturb them unnoticed.
    assert job_map["golden"] == {
        "demos/showcase/scenarios/golden/golden.yaml",
        "demos/showcase/scenarios/golden/golden_xcuitest.yaml",
    }


def test_ios_actuation_job_still_declares_the_paste_consent_scenario() -> None:
    # The same coverage pin, for the same reason (BE-0369): `paste_system_alert.yaml` is the only
    # place `prompt: paste` — and the cross-process paste consent itself — meets a real Simulator,
    # and it sits on the non-gating `actuation` job, where a silent drop shows no red X.
    text = lane_workflow_text("ios")
    assert text is not None
    declared = job_scenario_map(text)["actuation"]
    assert "demos/showcase/scenarios/paste_system_alert.yaml" in declared, (
        "the `actuation` job no longer declares paste_system_alert.yaml — BE-0369's only on-device "
        "coverage of the paste-consent prompt"
    )


def test_ios_actuation_job_still_declares_the_authoring_scenarios() -> None:
    # A coverage pin, not a narrowing one (hence its own test): BE-0285 brought `extract`, `forEach`,
    # data-driven rows, and `relaunch` to iOS on the non-gating `actuation` job, which is the only
    # place any of them runs against a real Simulator. Dropping one from the workflow would retire that
    # coverage silently — the job is off the `E2E` gate, so nobody is watching a red X. The fast gate
    # cannot run a Simulator, but it can check the wiring.
    text = lane_workflow_text("ios")
    assert text is not None
    declared = job_scenario_map(text)["actuation"]
    authoring = {
        "demos/showcase/scenarios/extract.yaml",
        "demos/showcase/scenarios/foreach.yaml",
        "demos/showcase/scenarios/data_driven.yaml",
        "demos/showcase/scenarios/relaunch.yaml",
    }
    assert authoring <= declared, (
        f"the `actuation` job no longer declares {sorted(authoring - declared)} — BE-0285's only "
        "on-device iOS coverage of those scenario-authoring features"
    )


def test_android_and_web_workflows_have_no_scenario_keyed_jobs() -> None:
    # Neither lane drives the bajutsu-e2e action, so both maps are empty — every scenario-only change
    # on them is unattributable and falls back to the whole fleet (they keep today's behavior).
    for lane in ("android", "web"):
        text = lane_workflow_text(lane)
        assert text is not None
        assert job_scenario_map(text) == {}, lane


def test_no_ios_declared_scenario_includes_another_declared_scenario() -> None:
    # The one under-fire the whole-fleet fallback cannot catch: a job-declared scenario that `use`s
    # another job-declared scenario as a component. Editing the inner file would attribute only to
    # its own declaring jobs, silently missing the job that includes it transitively. Today no
    # CI-declared iOS scenario includes another (components live under `_components/`, never declared
    # by a job), so the narrowing is safe. Lock that in: if a future author makes one declared
    # scenario `use` another, this fails rather than letting the lane mis-narrow.
    repo_root = Path(__file__).resolve().parent.parent
    text = lane_workflow_text("ios")
    assert text is not None
    declared = set().union(*job_scenario_map(text).values())
    component_ref = re.compile(r"component:\s*([\w./-]+)")
    for scenario in declared:
        scenario_path = repo_root / scenario
        for ref in component_ref.findall(scenario_path.read_text(encoding="utf-8")):
            resolved = os.path.normpath(Path(scenario).parent / ref)
            assert resolved not in declared, (
                f"{scenario} includes CI-declared scenario {resolved} via `use` — editing "
                f"{resolved} would not fire {scenario}'s job. Move the shared steps into a "
                f"non-declared component under `_components/` to keep the narrowing safe."
            )


# --- Makefile-declared jobs keyed from the showcase Makefile (BE-0338) ----------------------------
# `codegen`, `visual`, and `network` declare their scenarios in `demos/showcase/Makefile` targets, not
# a workflow `scenarios:` input, so their attribution is read from the recipes and pinned here against
# the Makefile ground truth — the drift guard that lets the narrowing hold BE-0322's no-drift
# invariant.

_MAKEFILE_SAMPLE = """\
CONFIG := $(HERE)/showcase.config.yaml

ui-test:
\t# demos/showcase/scenarios/retired.yaml is a shell comment make never runs — must not be collected.
\tcd $(ROOT) && uv run bajutsu codegen demos/showcase/scenarios/components.yaml --target showcase-swiftui \\
\t\t--config $(CONFIG) -o demos/showcase/ios/swiftui/UITests/ComponentsUITests.swift
\tcd $(SWIFTUI) && xcodegen generate

# A comment between recipes must not leak the next target's scenarios into this one.
UITESTS := $(SWIFTUI)/UITests
ui-test-coverage:
\tcd $(ROOT) && uv run bajutsu codegen demos/showcase/scenarios/text_editing.yaml --target showcase-swiftui \\
\t\t--config $(CONFIG) -o $(UITESTS)/CoverageTextEditing.swift
\tcd $(ROOT) && uv run bajutsu codegen demos/showcase/scenarios/gestures.yaml --target showcase-swiftui \\
\t\t--config $(CONFIG) -o $(UITESTS)/CoverageGestures.swift

e2e-visual: swiftui-build
\tcd $(ROOT) && uv run --extra visual bajutsu run \\
\t\t--scenario demos/showcase/scenarios/visual/visual_ios.yaml \\
\t\t--target showcase-swiftui --udid $(SIM)

e2e-network: swiftui-build runner-build
\tcd $(ROOT) && uv run bajutsu run \\
\t\t--scenario demos/showcase/scenarios/network_mock.yaml \\
\t\t--scenario demos/showcase/scenarios/network_live.yaml \\
\t\t--target showcase-swiftui --udid $(SIM)
"""


def test_makefile_target_scenarios_reads_a_targets_recipe() -> None:
    # The reader collects every scenario path a target's tab-indented recipe names, and stops at the
    # next target — a prefix collision (`ui-test` vs `ui-test-coverage`) or a comment between recipes
    # must not bleed one target's scenarios into another's. A tab-then-`#` shell comment inside the
    # recipe names no scenario make runs, so `retired.yaml` in `ui-test` must not be collected.
    assert _makefile_target_scenarios(_MAKEFILE_SAMPLE, "ui-test") == {
        "demos/showcase/scenarios/components.yaml"
    }
    assert _makefile_target_scenarios(_MAKEFILE_SAMPLE, "ui-test-coverage") == {
        "demos/showcase/scenarios/text_editing.yaml",
        "demos/showcase/scenarios/gestures.yaml",
    }
    assert _makefile_target_scenarios(_MAKEFILE_SAMPLE, "e2e-visual") == {
        "demos/showcase/scenarios/visual/visual_ios.yaml"
    }
    assert _makefile_target_scenarios(_MAKEFILE_SAMPLE, "e2e-network") == {
        "demos/showcase/scenarios/network_mock.yaml",
        "demos/showcase/scenarios/network_live.yaml",
    }
    # A target that isn't present names nothing.
    assert _makefile_target_scenarios(_MAKEFILE_SAMPLE, "absent") == set()


def test_makefile_job_scenarios_folds_a_jobs_targets() -> None:
    job_scenarios = makefile_job_scenarios(_MAKEFILE_SAMPLE)
    # `codegen` unions its two targets; `visual` and `network` each have one.
    assert job_scenarios["codegen"] == {
        "demos/showcase/scenarios/components.yaml",
        "demos/showcase/scenarios/text_editing.yaml",
        "demos/showcase/scenarios/gestures.yaml",
    }
    assert job_scenarios["visual"] == {"demos/showcase/scenarios/visual/visual_ios.yaml"}
    assert job_scenarios["network"] == {
        "demos/showcase/scenarios/network_mock.yaml",
        "demos/showcase/scenarios/network_live.yaml",
    }


def test_makefile_job_scenarios_raises_when_a_target_names_no_scenario() -> None:
    # A recipe that names no scenario (a rename that outdated the target, or a parse failure) must
    # fail loud so the caller falls back to the whole lane rather than narrowing a job to nothing.
    empty = (
        "ui-test:\n\techo nothing here\n"
        "ui-test-coverage:\n\techo nor here\n"
        "e2e-visual:\n\techo none\n"
        "e2e-network:\n\techo none either\n"
    )
    with pytest.raises(ValueError, match="codegen"):
        makefile_job_scenarios(empty)


def test_makefile_job_scenarios_raises_on_the_network_targets_own_empty_recipe() -> None:
    # The `empty` / `half` fixtures below both raise on an earlier job, so neither observes
    # `network`'s own per-target path. Leave every other target scenario-bearing and empty only
    # `e2e-network`, so the raise has to name it.
    only_network_empty = _MAKEFILE_SAMPLE.replace(
        "\tcd $(ROOT) && uv run bajutsu run \\\n"
        "\t\t--scenario demos/showcase/scenarios/network_mock.yaml \\\n"
        "\t\t--scenario demos/showcase/scenarios/network_live.yaml \\\n",
        "\techo renamed away, names no scenario\n",
    )
    assert "network_mock.yaml" not in only_network_empty
    with pytest.raises(ValueError, match="e2e-network"):
        makefile_job_scenarios(only_network_empty)


def test_makefile_job_scenarios_raises_when_a_job_lists_no_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The empty tuple is the one input the per-target ladder below cannot see: it iterates zero times,
    # so the job would land in the map with an empty scenario set, be selected by nothing, and
    # silently never fire on a scenario-only change — an under-fire, not the over-fire this module
    # falls back to everywhere else.
    monkeypatch.setitem(_MAKEFILE_JOB_TARGETS, "network", ())
    with pytest.raises(ValueError, match="network"):
        makefile_job_scenarios(_MAKEFILE_SAMPLE)


def test_makefile_job_scenarios_raises_per_target_not_per_job() -> None:
    # Fail-closed per target: if one of a job's targets goes empty (renamed/broken) while another
    # still yields scenarios, the union is non-empty — but the empty target's scenarios silently drop
    # from the job's set, under-attributing it. The raise must fire on the empty target, naming it, so
    # the caller falls back to the whole lane rather than narrowing a job on a half-read map.
    half = (
        "ui-test:\n"
        "\techo renamed away, names no scenario\n"
        "ui-test-coverage:\n"
        "\tuv run bajutsu codegen demos/showcase/scenarios/text_editing.yaml -o x.swift\n"
        "e2e-visual:\n"
        "\tuv run bajutsu run --scenario demos/showcase/scenarios/visual/visual_ios.yaml\n"
        "e2e-network:\n"
        "\tuv run bajutsu run --scenario demos/showcase/scenarios/network_mock.yaml\n"
    )
    with pytest.raises(ValueError, match="ui-test"):
        makefile_job_scenarios(half)


def test_makefile_declared_scenarios_match_the_targets() -> None:
    # BE-0338 drift guard — the linchpin. The Makefile-declared jobs' attribution is read from the
    # showcase Makefile recipes (never a second hand-written copy), but pin the extracted set against
    # an explicit expectation so a Makefile edit that adds or drops a scenario from `ui-test` /
    # `ui-test-coverage` / `e2e-visual` / `e2e-network` fails `make check` until the attribution is
    # consciously moved with it. Without this the attribution is exactly the drift BE-0322's design
    # removed; with it, it is as ground-truth-bound as the action-input map.
    text = showcase_makefile_text()
    assert text is not None
    assert makefile_job_scenarios(text) == {
        "codegen": {
            "demos/showcase/scenarios/components.yaml",
            "demos/showcase/scenarios/text_editing.yaml",
            "demos/showcase/scenarios/gestures.yaml",
            "demos/showcase/scenarios/gestures_multitouch.yaml",
            "demos/showcase/scenarios/codegen_extra.yaml",
        },
        "visual": {"demos/showcase/scenarios/visual/visual_ios.yaml"},
        "network": {
            "demos/showcase/scenarios/network_mock.yaml",
            "demos/showcase/scenarios/network_live.yaml",
        },
    }


def test_lane_job_scenario_map_folds_the_makefile_jobs_on_ios_only() -> None:
    # On iOS the combined map carries the workflow-declared jobs *and* the Makefile-declared
    # `codegen` / `visual` / `network`. On Android / web the Makefile jobs are not folded — they key no
    # jobs on scenarios (BE-0338 is iOS-only), so the combined map equals the (empty) workflow map.
    ios_text = lane_workflow_text("ios")
    assert ios_text is not None
    ios_map = lane_job_scenario_map("ios", ios_text)
    assert set(ios_map) == {
        "run",
        "actuation",
        "golden",
        "bundled-runner",
        "codegen",
        "visual",
        "network",
    }
    assert ios_map["codegen"] == {
        "demos/showcase/scenarios/components.yaml",
        "demos/showcase/scenarios/text_editing.yaml",
        "demos/showcase/scenarios/gestures.yaml",
        "demos/showcase/scenarios/gestures_multitouch.yaml",
        "demos/showcase/scenarios/codegen_extra.yaml",
    }
    assert ios_map["visual"] == {"demos/showcase/scenarios/visual/visual_ios.yaml"}
    assert ios_map["network"] == {
        "demos/showcase/scenarios/network_mock.yaml",
        "demos/showcase/scenarios/network_live.yaml",
    }
    for lane in ("android", "web"):
        text = lane_workflow_text(lane)
        assert text is not None
        assert lane_job_scenario_map(lane, text) == {}, lane


def test_the_makefile_declared_jobs_carry_the_scenario_keyed_guard() -> None:
    # Unit 4 wiring: each of these jobs must guard on its `affected` slot, not fire on bare
    # `relevant`. A revert of the `if:` (back to `relevant == 'true'`) would silently un-narrow it, so
    # pin the guard's presence here alongside the map-key match test above.
    text = lane_workflow_text("ios")
    assert text is not None
    for job in _MAKEFILE_JOB_TARGETS:
        assert f"contains(fromJSON(needs.changes.outputs.affected), '{job}')" in text, job


def test_makefile_target_job_map_covers_only_the_makefile_declared_jobs() -> None:
    # `conformance` stays a dimension job (it drives the whole harness, declaring no scenario subset),
    # so only `codegen`, `visual`, and `network` are Makefile-keyed. Pin the mapping so a future
    # addition is a conscious edit here.
    assert set(_MAKEFILE_JOB_TARGETS) == {"codegen", "visual", "network"}


def test_makefile_job_targets_match_the_workflow_make_invocations() -> None:
    # Close the loop between `_MAKEFILE_JOB_TARGETS` and the actual workflow (BE-0338 follow-up), in
    # BOTH directions, so the dict can neither list a target the job no longer runs nor omit a
    # scenario-bearing target the job does run:
    #   - rename/removal: every target the dict lists must appear as a `make -C demos/showcase
    #     <target>` step in that job's block — so renaming a listed target out of the workflow (and
    #     `makefile_job_scenarios` then reading a target the job no longer runs) fails here.
    #   - addition: every `make -C demos/showcase <target>` step in the job's block whose Makefile
    #     recipe is *scenario-bearing* must be in the dict — so adding a new scenario target to a job
    #     without listing it (leaving its scenario silently unattributed, an under-fire) fails here.
    # The addition check filters on scenario-bearing recipes via `_makefile_target_scenarios`, so the
    # non-scenario build targets these jobs legitimately invoke (`swiftui-build`, `runner-build`) are
    # ignored rather than forced into the dict.
    text = lane_workflow_text("ios")
    assert text is not None
    makefile = showcase_makefile_text()
    assert makefile is not None
    # Extract a rough "job section" for each job: collect lines from the job's header until the next
    # same-level (2-space-indented) job header.
    job_section_re = re.compile(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$")
    make_step_re = re.compile(r"make -C demos/showcase (\S+)")
    sections: dict[str, list[str]] = {}
    current_job: str | None = None
    for line in text.splitlines():
        if m := job_section_re.match(line):
            current_job = m.group(1)
        if current_job is not None:
            sections.setdefault(current_job, []).append(line)
    for job, targets in _MAKEFILE_JOB_TARGETS.items():
        assert job in sections, f"job {job!r} not found in ios-e2e.yml"
        section_text = "\n".join(sections[job])
        # rename/removal: each listed target is still invoked.
        for target in targets:
            assert f"make -C demos/showcase {target}" in section_text, (
                f"ios-e2e.yml job {job!r} has no `make -C demos/showcase {target}` step, but "
                f"_MAKEFILE_JOB_TARGETS[{job!r}] lists it — update the dict or the workflow."
            )
        # addition: each scenario-bearing target the job invokes is listed.
        for invoked in make_step_re.findall(section_text):
            if _makefile_target_scenarios(makefile, invoked):
                assert invoked in targets, (
                    f"ios-e2e.yml job {job!r} runs `make -C demos/showcase {invoked}`, whose recipe "
                    f"names scenarios, but _MAKEFILE_JOB_TARGETS[{job!r}] omits it — a change to that "
                    f"scenario would not fire {job!r}. Add {invoked!r} to the dict."
                )


# --- main() end to end over the real iOS workflow (BE-0322) --------------------------------------


def test_main_narrows_a_scenario_only_ios_change_to_the_affected_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A change confined to gestures_multitouch.yaml is scenario-only and reaches the `run` job (which
    # declares it in the workflow) and — since BE-0338 — the `codegen` job, which codegens it via the
    # `ui-test-coverage` Makefile target; main() emits shared=false and the narrowed affected set. The
    # job-to-scenario map is read from the real workflow + showcase Makefile (via lane_workflow_text /
    # showcase_makefile_text), while the changed file lives in the temp repo.
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")
    main_tip = _commit(tmp_path, "bajutsu/runner/pipeline.py", "unrelated on main")
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "demos/showcase/scenarios/gestures_multitouch.yaml", "gesture edit")

    output = tmp_path / "github_output"
    monkeypatch.setenv("BASE_SHA", main_tip)
    monkeypatch.setenv("HEAD_SHA", pr_tip)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert main() == 0
    assert output.read_text(encoding="utf-8") == (
        'relevant=true\nshared=false\naffected=["codegen", "run"]\npool=false\n'
    )


def test_main_narrows_a_shared_scenario_to_every_job_that_declares_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # smoke.yaml is declared by both `run` and `bundled-runner`, so a change to it fires both — the
    # affected array is sorted for a deterministic output.
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")
    main_tip = _commit(tmp_path, "main_only.txt", "unrelated on main")
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "demos/showcase/scenarios/smoke.yaml", "smoke edit")

    output = tmp_path / "github_output"
    monkeypatch.setenv("BASE_SHA", main_tip)
    monkeypatch.setenv("HEAD_SHA", pr_tip)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert main() == 0
    assert output.read_text(encoding="utf-8") == (
        'relevant=true\nshared=false\naffected=["bundled-runner", "run"]\npool=false\n'
    )


def test_main_falls_back_to_the_whole_fleet_for_an_unattributable_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # tabs.yaml is a scenario file no CI job declares (a scenario the lane never runs). It is
    # scenario-only, but unattributable to a job, so main() fires the whole lane (shared=true) rather
    # than narrowing to nothing — the safe over-selection.
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")
    main_tip = _commit(tmp_path, "main_only.txt", "unrelated on main")
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "demos/showcase/scenarios/tabs.yaml", "unrun scenario edit")

    output = tmp_path / "github_output"
    monkeypatch.setenv("BASE_SHA", main_tip)
    monkeypatch.setenv("HEAD_SHA", pr_tip)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert main() == 0
    assert (
        output.read_text(encoding="utf-8")
        == "relevant=true\nshared=true\naffected=[]\npool=false\n"
    )


def test_main_keeps_the_android_lane_whole_fleet_on_a_scenario_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Android lane has no scenario-keyed jobs, so a scenario-only change there is unattributable
    # and fires the whole lane (shared=true) — the narrowing is a no-op for it, exactly as designed.
    # The same change would narrow on the iOS lane; here it must not.
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")
    main_tip = _commit(tmp_path, "main_only.txt", "unrelated on main")
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "demos/showcase/scenarios/smoke.yaml", "smoke edit")

    output = tmp_path / "github_output"
    monkeypatch.setenv("E2E_LANE", "android")
    monkeypatch.setenv("BASE_SHA", main_tip)
    monkeypatch.setenv("HEAD_SHA", pr_tip)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert main() == 0
    assert (
        output.read_text(encoding="utf-8")
        == "relevant=true\nshared=true\naffected=[]\npool=false\n"
    )


def test_main_narrows_a_codegen_scenario_change_to_the_codegen_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BE-0338: components.yaml is codegen-only (declared by `make ui-test`, no workflow `scenarios:`
    # input), so a change confined to it narrows to just the `codegen` job — the two-Simulator lane no
    # longer boots for a scenario neither `run` nor `visual` exercises. The map folds the showcase
    # Makefile ground truth read from the real repo, while the changed file lives in the temp repo.
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")
    main_tip = _commit(tmp_path, "main_only.txt", "unrelated on main")
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "demos/showcase/scenarios/components.yaml", "components edit")

    output = tmp_path / "github_output"
    monkeypatch.setenv("BASE_SHA", main_tip)
    monkeypatch.setenv("HEAD_SHA", pr_tip)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert main() == 0
    assert (
        output.read_text(encoding="utf-8")
        == 'relevant=true\nshared=false\naffected=["codegen"]\npool=false\n'
    )


def test_main_narrows_a_network_scenario_change_to_the_network_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BE-0282: the two network scenarios are declared by `make e2e-network` alone, so a change to
    # either narrows to just the `network` job. Before that target existed they were attributable to
    # no job and fell back to the whole lane; the risk of the narrowing is the other direction — an
    # `affected` that came back empty would skip the job while `E2E (iOS)` still reported green, so
    # pin the positive selection end to end, as the codegen / visual pair above does.
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")
    main_tip = _commit(tmp_path, "main_only.txt", "unrelated on main")
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "demos/showcase/scenarios/network_mock.yaml", "mock edit")

    output = tmp_path / "github_output"
    monkeypatch.setenv("BASE_SHA", main_tip)
    monkeypatch.setenv("HEAD_SHA", pr_tip)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert main() == 0
    assert (
        output.read_text(encoding="utf-8")
        == 'relevant=true\nshared=false\naffected=["network"]\npool=false\n'
    )


def test_main_narrows_a_visual_scenario_change_to_the_visual_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BE-0338: visual/visual_ios.yaml is the pixel VRT `make e2e-visual` runs, so a change to it
    # narrows to just the `visual` job.
    _init_repo(tmp_path, monkeypatch)
    _commit(tmp_path, "README.md", "seed")
    _git(tmp_path, "branch", "pr")
    main_tip = _commit(tmp_path, "main_only.txt", "unrelated on main")
    _git(tmp_path, "checkout", "-q", "pr")
    pr_tip = _commit(tmp_path, "demos/showcase/scenarios/visual/visual_ios.yaml", "vrt edit")

    output = tmp_path / "github_output"
    monkeypatch.setenv("BASE_SHA", main_tip)
    monkeypatch.setenv("HEAD_SHA", pr_tip)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert main() == 0
    assert (
        output.read_text(encoding="utf-8")
        == 'relevant=true\nshared=false\naffected=["visual"]\npool=false\n'
    )
