"""Tests for the on-demand app build (BE-0063): `run` builds a Git-sourced app from the checkout root."""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.runner.build import BuildError, build_if_missing


def test_build_runs_from_the_checkout_root_and_produces_the_binary(tmp_path: Path) -> None:
    # The build command's relative parts are resolved against `cwd` (the checkout root), exactly
    # as a Git-sourced config expects — here `app/Demo.app` is created under the root, not the
    # process's working directory.
    app_path = tmp_path / "app" / "Demo.app"
    build_if_missing("mkdir -p app/Demo.app", str(app_path), cwd=tmp_path)
    assert app_path.exists()


def test_build_is_a_noop_when_the_binary_already_exists(tmp_path: Path) -> None:
    app_path = tmp_path / "Demo.app"
    app_path.mkdir()
    # A build command that would fail proves it is never run when the binary is present.
    build_if_missing("false", str(app_path), cwd=tmp_path)


def test_build_is_a_noop_without_a_build_command_or_app_path(tmp_path: Path) -> None:
    build_if_missing(None, str(tmp_path / "Demo.app"), cwd=tmp_path)
    build_if_missing("mkdir x", None, cwd=tmp_path)
    assert not (tmp_path / "x").exists()  # no app_path → nothing built


def test_failing_build_raises_build_error(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="build failed"):
        build_if_missing("false", str(tmp_path / "Demo.app"), cwd=tmp_path)
