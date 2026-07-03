"""Tests for the shared `gh` CLI wrapper (BE-0149).

A thin subprocess wrapper, so these just pin the two call shapes its callers rely on: ``run`` raises
on failure and returns stdout when asked, ``run_allow_failure`` never raises and hands back the full
completed-process for the caller to inspect.
"""

from __future__ import annotations

import subprocess

import pytest

from scripts import gh_cli


def test_run_returns_stdout_when_capturing() -> None:
    assert gh_cli.run(["--version"], capture=True).startswith("gh version")


def test_run_raises_on_a_bad_command() -> None:
    with pytest.raises(subprocess.CalledProcessError):
        gh_cli.run(["not-a-real-gh-subcommand"])


def test_run_allow_failure_reports_the_return_code_without_raising() -> None:
    result = gh_cli.run_allow_failure(["not-a-real-gh-subcommand"])
    assert result.returncode != 0
