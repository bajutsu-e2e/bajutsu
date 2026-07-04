"""Tests for the shared `gh` CLI wrapper (BE-0149).

A thin subprocess wrapper, so these pin the two call shapes its callers rely on without shelling out
to a real ``gh`` binary: ``subprocess.run`` is stubbed, keeping the suite hermetic (no dependency on
the GitHub CLI being installed) — the same "network glue never runs inside ``make check``" contract
the callers document. ``run`` raises on failure and returns stdout when asked; ``run_allow_failure``
never raises and hands back the completed process for the caller to inspect.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from scripts import gh_cli


class _FakeRun:
    """A ``subprocess.run`` stand-in: records each call and returns a canned ``CompletedProcess``,
    raising ``CalledProcessError`` exactly when the real one would (``check=True`` and non-zero)."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        self.kwargs.append(kwargs)
        if kwargs.get("check") and self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, cmd, self.stdout, "")
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.stdout, stderr="")


def test_run_returns_stdout_when_capturing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRun(stdout="gh version 2.40.0\n")
    monkeypatch.setattr(subprocess, "run", fake)
    assert gh_cli.run(["--version"], capture=True) == "gh version 2.40.0\n"
    assert fake.calls[0] == ["gh", "--version"]
    assert fake.kwargs[0]["check"] is True
    assert fake.kwargs[0]["capture_output"] is True


def test_run_without_capture_returns_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRun(stdout="unused")
    monkeypatch.setattr(subprocess, "run", fake)
    assert gh_cli.run(["pr", "comment", "1"]) == ""
    assert fake.kwargs[0]["capture_output"] is False


def test_run_raises_on_a_bad_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _FakeRun(returncode=1))
    with pytest.raises(subprocess.CalledProcessError):
        gh_cli.run(["not-a-real-gh-subcommand"])


def test_run_allow_failure_reports_the_return_code_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRun(returncode=1)
    monkeypatch.setattr(subprocess, "run", fake)
    result = gh_cli.run_allow_failure(["not-a-real-gh-subcommand"])
    assert result.returncode == 1
    assert fake.kwargs[0]["check"] is False
