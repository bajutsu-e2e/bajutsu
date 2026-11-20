"""Build the app's binary before a run: on demand when it is missing, or always on request.

A Git-sourced config (`bajutsu run --config github:…`) is materialized into a content-addressed
checkout that holds no built binary, and there is no "first" in which to build it by hand (BE-0063).
A local checkout can be in the same position — after a clean, or on a fresh machine — so `run`
builds a missing binary for either source, executing the config's `subject.build` command from the
directory its relative parts are rooted in: the checkout root, or the local config file's own
directory. `--rebuild` forces the same command even when the binary exists, so a source change is
never tested against a stale build.

It stays inside the prime directives: a build is deterministic infrastructure (a shell command, no
LLM), and a failed build raises rather than letting the run start against a missing binary.

The serve control plane has its own build (`bajutsu/serve/jobs.py::_build_app`) for its
long-running, cancellable, log-streaming job model; this is the synchronous, raising CLI counterpart.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from bajutsu.common.capability.command_policy import SUBJECT_BUILD_FIELD
from bajutsu.common.capability import command_gate
from bajutsu.common.capability.command_gate import CommandRefused


class BuildError(RuntimeError):
    """A needed build command failed, so the run must not start against a missing binary."""


def _build_env() -> dict[str, str]:
    # The build child's env, with the venv's bin dir on PATH so a venv-installed build tool resolves
    # — matching serve's `_spawn_env`, so a Git-sourced build behaves the same either way.
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return env


def build_app(build: str | None, app_path: str | None, *, cwd: Path, force: bool = False) -> None:
    """Run *build* from *cwd* when *app_path* is set but missing, or whenever *force* is set.

    A no-op when there is nothing to build: no `build` command, or — without *force* — no
    `app_path` or a binary that already exists, so an existing build is reused untouched.

    The worker's command policy decides whether a needed build runs at all (BE-0492): a mode that
    does not run builds for the config's source refuses it, naming the mode, so the run never starts
    against a missing or stale binary and never runs a command the worker's operator ruled out.

    Args:
        build: The config's `subject.build` shell command, or None.
        app_path: The expected built-binary path (absolute, rebased against the config's root), or
            None.
        cwd: The working directory the build runs in — a Git checkout root, or the local config
            file's own directory.
        force: Build even when the binary already exists (`run --rebuild`).

    Raises:
        BuildError: The worker's command policy does not run the build, or the command is
            unparseable, exited non-zero, or could not be spawned.
    """
    if not build:
        return
    if not force and (not app_path or Path(app_path).exists()):
        return
    gate = command_gate.current()
    if gate.check("build", SUBJECT_BUILD_FIELD, build) != "host":
        try:
            gate.refuse(SUBJECT_BUILD_FIELD, build)
        except CommandRefused as e:
            raise BuildError(str(e)) from None
    if force:
        sys.stderr.write(f"rebuilding the app: {build}\n")
    else:
        sys.stderr.write(f"app binary missing ({app_path}) — building: {build}\n")
    try:
        argv = shlex.split(build)  # a mis-quoted build string is a clean failure, not a traceback
    except ValueError as e:
        raise BuildError(f"build command is not valid shell syntax: {build!r} ({e})") from e
    try:
        code = subprocess.run(argv, cwd=cwd, check=False, env=_build_env()).returncode
    except OSError as e:
        raise BuildError(f"build failed: {e}") from e
    gate.record(SUBJECT_BUILD_FIELD, build, exit_code=code, cwd=cwd)
    if code != 0:
        raise BuildError(f"build failed (exit {code}): {build}")
