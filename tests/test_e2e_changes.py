"""Tests for scripts/e2e_changes.py — the per-lane E2E relevance filter (each lane's `changes` job).

The three E2E lanes (ios / android / web) each carry a required aggregator, so none can be path-gated
at the trigger; instead this filter decides, per lane, whether the heavy jobs run. These tests pin the
pieces: the shared run-path core and each lane's own surface (`is_relevant`, keyed by lane), and — the
regression this script exists for — that `changed_files` uses a merge-base (three-dot) diff, so a PR
whose base branch has moved on isn't charged for files it never touched.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.e2e_changes import changed_files, is_relevant, main


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
    # The top-level allow-list: only the single-level modules the on-device run / codegen / record
    # path actually imports (the run loop, assertions, the element model, the driver helpers, the
    # visual/golden dimensions, codegen, plus the run-pipeline's direct dependencies: evidence,
    # redaction, artifact_perms, mailbox; and record.py's direct imports: agent, crawl, handoff).
    # Each is listed explicitly rather than swept by a `bajutsu/*.py` blanket, which also caught
    # serve/analytics/crawl modules that never run here.
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


def test_agent_factory_is_not_relevant_by_parity() -> None:
    # agents/factory.py (was agent_factory.py, the renamed agents.py) was never on the allow-list:
    # only agent.py (now agents/protocols.py) was. cli/commands/record.py does import make_agent
    # from it, so an argument exists for listing it — but that is a trigger-surface change, not a
    # rename, so the BE-0246/BE-0257 renames keep exact parity and leave closing that latent gap to
    # a separate decision.
    assert is_relevant(["bajutsu/agents/factory.py"]) is False


def test_non_run_path_top_level_modules_are_not_relevant() -> None:
    # The regression this fixes: a serve/analytics/crawl module lives at the top level too, but the
    # on-device jobs never import it, so touching it must not burn the metered macOS jobs. (PR #936,
    # a serve-only change to bajutsu/stats.py, wrongly fired all four.)
    for module in (
        "bajutsu/analysis/stats.py",
        "bajutsu/analysis/audit.py",
        "bajutsu/analysis/coverage.py",
        "bajutsu/analytics/stats.py",
        "bajutsu/agents/alerts.py",
        "bajutsu/github.py",
        # The crawl engine core/serialize/__init__ trigger (above), but the periphery siblings in the
        # same package do not — the on-device run never imports them, so `crawl/**` must not be swept
        # wholesale. All four are pinned so the regex boundary is fully covered, not just sampled.
        "bajutsu/crawl/guide.py",
        "bajutsu/crawl/report.py",
        "bajutsu/crawl/repro.py",
        "bajutsu/crawl/flows.py",
        "bajutsu/crawl/tabs.py",
    ):
        assert is_relevant([module]) is False, module


def test_untouched_subpackage_is_not_relevant() -> None:
    # ...and a subpackage the E2E never exercises (serve/mcp/report/templates) is not.
    assert is_relevant(["bajutsu/mcp/server.py"]) is False
    assert is_relevant(["bajutsu/report/manifest.py"]) is False


def test_only_listed_cli_commands_are_relevant() -> None:
    assert is_relevant(["bajutsu/cli/commands/run.py"]) is True
    assert is_relevant(["bajutsu/cli/commands/trace.py"]) is False


def test_conformance_suite_is_relevant_but_other_tests_are_not() -> None:
    # The on-device conformance suite (BE-0114) runs in these jobs, so a change to its contract or
    # its harness must re-run them; an ordinary unit test the E2E never executes must not.
    assert is_relevant(["tests/driver_conformance.py"]) is True
    assert is_relevant(["tests/test_driver_conformance_ondevice.py"]) is True
    assert is_relevant(["tests/test_e2e_changes.py"]) is False


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
        assert is_relevant(["uv.lock"], lane) is True, lane


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
    # The web lane exercises every driver, the serve backend + templates (the serve-UI dogfood), the
    # web + serve-ui demos, its own conformance harness, and its own workflow file.
    assert is_relevant(["bajutsu/drivers/playwright.py"], "web") is True
    assert is_relevant(["bajutsu/serve/app.py"], "web") is True
    assert is_relevant(["bajutsu/templates/report.html"], "web") is True
    assert is_relevant(["demos/serve-ui/scenario.yaml"], "web") is True
    assert is_relevant(["demos/web/scenario.yaml"], "web") is True
    assert is_relevant(["tests/test_driver_conformance_web.py"], "web") is True
    assert is_relevant([".github/workflows/web-e2e.yml"], "web") is True
    # ...but not the Android app SDK, the iOS showcase, or another lane's workflow.
    assert is_relevant(["BajutsuAndroid/src/Clipboard.kt"], "web") is False
    assert is_relevant(["demos/showcase/ios/swiftui/App.swift"], "web") is False
    assert is_relevant([".github/workflows/android-e2e.yml"], "web") is False


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

    for lane, expected in (("android", "true"), ("web", "false")):
        output = tmp_path / f"github_output_{lane}"
        monkeypatch.setenv("E2E_LANE", lane)
        monkeypatch.setenv("BASE_SHA", main_tip)
        monkeypatch.setenv("HEAD_SHA", pr_tip)
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        assert main() == 0
        assert output.read_text(encoding="utf-8") == f"relevant={expected}\n", lane


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
    assert output.read_text(encoding="utf-8") == "relevant=true\n"


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
    assert output.read_text(encoding="utf-8") == "relevant=false\n"
