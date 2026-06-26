"""Build the app's binary on demand before a run (BE-0063).

A Git-sourced config (`bajutsu run --config github:…`) is materialized into a content-addressed
checkout that holds no built binary, and there is no "first" in which to build it by hand. So `run`
builds it here when it is missing — executing the config's `build` command with the **checkout root**
as the working directory, since the command's relative parts (`make -C demos/features …`) are rooted
there, not at the caller's current directory.

It stays inside the prime directives: a build is deterministic infrastructure (a shell command, no
LLM), and a failed build raises rather than letting the run start against a missing binary.

The serve control plane has its own build-on-demand (`bajutsu/serve/jobs.py::_build_app`) for its
long-running, cancellable, log-streaming job model; this is the synchronous, raising CLI counterpart.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path


class BuildError(RuntimeError):
    """A needed build command failed, so the run must not start against a missing binary."""


def build_if_missing(build: str | None, app_path: str | None, *, cwd: Path) -> None:
    """Run *build* from *cwd* when *app_path* is set but its binary is missing.

    A no-op when there is nothing to build — no `build` command, no `app_path`, or the binary
    already exists — so a checkout that already carries the built app is reused untouched.

    Args:
        build: The config's `build` shell command, or None.
        app_path: The expected built-binary path (absolute, rebased against the checkout), or None.
        cwd: The working directory the build runs in — the materialized checkout root.

    Raises:
        BuildError: The build command exited non-zero, or could not be spawned.
    """
    if not build or not app_path or Path(app_path).exists():
        return
    sys.stderr.write(f"app binary missing ({app_path}) — building: {build}\n")
    try:
        code = subprocess.run(shlex.split(build), cwd=cwd, check=False).returncode
    except OSError as e:
        raise BuildError(f"build failed: {e}") from e
    if code != 0:
        raise BuildError(f"build failed (exit {code}): {build}")
