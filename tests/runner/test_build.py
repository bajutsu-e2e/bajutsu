"""Tests for the app build before a run: on demand when the binary is missing, or forced (`--rebuild`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.common.config import RUNNER_BUILD_WITHHELD_ENV
from bajutsu.common.runner.build import BuildError, build_app


def test_build_runs_from_the_checkout_root_and_produces_the_binary(tmp_path: Path) -> None:
    # The build command's relative parts are resolved against `cwd` (the checkout root), exactly
    # as a Git-sourced config expects — here `app/Demo.app` is created under the root, not the
    # process's working directory.
    app_path = tmp_path / "app" / "Demo.app"
    build_app("mkdir -p app/Demo.app", str(app_path), cwd=tmp_path)
    assert app_path.exists()


def test_build_is_a_noop_when_the_binary_already_exists(tmp_path: Path) -> None:
    app_path = tmp_path / "Demo.app"
    app_path.mkdir()
    # A build command that would fail proves it is never run when the binary is present.
    build_app("false", str(app_path), cwd=tmp_path)


def test_build_is_a_noop_without_a_build_command_or_app_path(tmp_path: Path) -> None:
    build_app(None, str(tmp_path / "Demo.app"), cwd=tmp_path)
    build_app("mkdir x", None, cwd=tmp_path)
    assert not (tmp_path / "x").exists()  # no app_path → nothing built


def test_failing_build_raises_build_error(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="build failed"):
        build_app("false", str(tmp_path / "Demo.app"), cwd=tmp_path)


def test_unparseable_build_string_raises_build_error(tmp_path: Path) -> None:
    # A mis-quoted build command fails cleanly (BuildError → exit 2), not with a shlex traceback.
    with pytest.raises(BuildError, match="not valid shell syntax"):
        build_app("make 'unterminated", str(tmp_path / "Demo.app"), cwd=tmp_path)


def test_a_forced_build_runs_even_when_the_binary_exists(tmp_path: Path) -> None:
    # `--rebuild`: the existing binary no longer short-circuits the build, so a source change is
    # picked up; the marker proves the command ran.
    app_path = tmp_path / "Demo.app"
    app_path.mkdir()
    build_app("touch rebuilt", str(app_path), cwd=tmp_path, force=True)
    assert (tmp_path / "rebuilt").exists()


def test_a_forced_build_needs_no_app_path(tmp_path: Path) -> None:
    build_app("touch rebuilt", None, cwd=tmp_path, force=True)
    assert (tmp_path / "rebuilt").exists()


def test_a_forced_build_without_a_command_is_a_noop(tmp_path: Path) -> None:
    build_app(None, str(tmp_path / "Demo.app"), cwd=tmp_path, force=True)


def test_a_withheld_on_demand_build_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A run serve spawned for an untrusted config must not execute the config's build: the missing
    # binary is left to fail the run, as it did before a local config built on demand.
    monkeypatch.setenv(RUNNER_BUILD_WITHHELD_ENV, "1")
    build_app("touch built", str(tmp_path / "Demo.app"), cwd=tmp_path)
    assert not (tmp_path / "built").exists()


def test_a_withheld_forced_build_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUNNER_BUILD_WITHHELD_ENV, "1")
    with pytest.raises(BuildError, match="withheld"):
        build_app("touch built", None, cwd=tmp_path, force=True)
    assert not (tmp_path / "built").exists()
