"""Tests for scripts/e2e_changes.py — the E2E relevance filter (e2e.yml's `changes` job).

The on-device macOS jobs bill at 10x, so e2e.yml only fires them when a PR touches what they
exercise. These tests pin the two pieces: the pure positive-list (`is_relevant`) and — the
regression this script exists for — that `changed_files` uses a merge-base (three-dot) diff, so a
PR whose base branch has moved on isn't charged for files it never touched.
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
        "bajutsu/assertions.py",
        "bajutsu/elements.py",
        "bajutsu/visual.py",
        "bajutsu/golden.py",
        "bajutsu/codegen/emit.py",
        "bajutsu/record.py",
        "bajutsu/adb.py",
        "bajutsu/simctl.py",
        # runner/pipeline.py and orchestrator/loop.py unconditional imports
        "bajutsu/evidence.py",
        "bajutsu/redaction.py",
        "bajutsu/artifact_perms.py",
        "bajutsu/mailbox.py",
        "bajutsu/intervals.py",
        # record.py unconditionally imports the Agent/EnrichmentAgent protocols from
        # agent_protocols (record is an E2E verb), mirroring the old agent.py entry. Its sibling
        # agent_factory (the old agents.py) is deliberately excluded — see the parity test below.
        "bajutsu/agent_protocols.py",
        "bajutsu/crawl/core.py",
        # record imports `screen_identity` through the package re-export, so `__init__` is on the
        # on-device import path too (its periphery siblings are not — see the parity test below).
        "bajutsu/crawl/__init__.py",
        "bajutsu/handoff.py",
    ):
        assert is_relevant([module]) is True, module


def test_agent_factory_is_not_relevant_by_parity() -> None:
    # agent_factory.py is the renamed agents.py, which was never on the allow-list: only agent.py
    # (now agent_protocols.py) was. cli/commands/record.py does import make_agent from it, so an
    # argument exists for listing it — but that is a trigger-surface change, not a rename, so the
    # BE-0246 rename keeps exact parity and leaves closing that latent gap to a separate decision.
    assert is_relevant(["bajutsu/agent_factory.py"]) is False


def test_non_run_path_top_level_modules_are_not_relevant() -> None:
    # The regression this fixes: a serve/analytics/crawl module lives at the top level too, but the
    # on-device jobs never import it, so touching it must not burn the metered macOS jobs. (PR #936,
    # a serve-only change to bajutsu/stats.py, wrongly fired all four.)
    for module in (
        "bajutsu/stats.py",
        "bajutsu/audit.py",
        "bajutsu/coverage.py",
        "bajutsu/usage_stats.py",
        "bajutsu/alerts.py",
        "bajutsu/github.py",
        # The crawl engine core triggers (above), but its periphery siblings in the same package
        # do not — the on-device run never imports them, so `crawl/**` must not be swept wholesale.
        "bajutsu/crawl/guide.py",
        "bajutsu/crawl/report.py",
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
    assert is_relevant([".github/workflows/e2e.yml"]) is True
    assert is_relevant([".github/workflows/ci.yml"]) is False


def test_any_relevant_path_amid_irrelevant_ones_triggers() -> None:
    assert is_relevant(["roadmaps/README.md", "docs/foo.md", "BajutsuKit/Sources/x.swift"]) is True


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
