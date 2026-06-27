"""Run an uploaded bundle's `launchServer.cmd` inside a throwaway, hardened Docker container.

`serve --upload-exec=sandbox` (BE-0090) routes an *uploaded* bundle's server command here instead of
spawning it on the bare host (`launch_server.py`'s `Popen`), so a hostile `cmd` reaches nothing but a
disposable container. The lifecycle mirrors `start_launch_server` — probe `readyUrl`, reuse if it
already answers, else bring the command up and wait on the same readiness condition (never a fixed
sleep) — but the bring-up is a container, torn down with `docker rm -f` rather than a process-group
kill. Docker is reached through two injected seams (`run_fn` for the long-running container, `exec_fn`
for the short `build` / `rm` commands), so every decision and argv is testable without a daemon; only
the real `docker run` / `docker build` is exercised outside the gate.
"""

from __future__ import annotations

import contextlib
import shlex
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from bajutsu.config import Effective, LaunchServer
from bajutsu.runner.launch_server import _POLL_INTERVAL, _default_log, _probe

# The container runs as a long-lived process we must poll for early exit; the short docker commands
# (`build`, `rm`) run to completion. Defaults reach the real daemon; tests inject fakes.
RunFn = Callable[[list[str]], "subprocess.Popen[bytes]"]
ExecFn = Callable[[list[str]], None]

_WORKDIR = "/bundle"  # where the bundle is bind-mounted read-only inside the container
# Resource caps for the disposable container. Conservative, documented defaults — a hostile cmd
# cannot exhaust the host even before the deeper per-tenant isolation of BE-0015 / BE-0016.
_USER = "65534:65534"  # nobody:nogroup — never root inside the container
_CPUS = "1.0"
_MEMORY = "512m"
_PIDS_LIMIT = "256"


class SandboxError(RuntimeError):
    """A sandboxed launchServer could not run. Raised loudly — never a fallback to bare-host."""


def _real_run(argv: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _real_exec(argv: list[str]) -> None:
    cmd = " ".join(argv)
    try:
        subprocess.run(argv, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        # Surface docker's own diagnostic (missing image, build syntax error, …); without it a failure
        # is just "docker command failed" with no clue why.
        detail = (e.stderr or e.stdout or b"").decode(errors="replace").strip()
        raise SandboxError(f"docker command failed: {cmd}{f' — {detail}' if detail else ''}") from e
    except OSError as e:  # docker not installed / not on PATH
        raise SandboxError(f"docker command failed: {cmd} — {e}") from e


def _container_name() -> str:
    return f"bajutsu-sandbox-{uuid.uuid4().hex[:12]}"


def build_image_argv(dockerfile: str, bundle_cwd: str, tag: str) -> list[str]:
    """`docker build` argv that builds the bundle's `dockerfile` into `tag` (context = the bundle)."""
    return ["docker", "build", "-f", str(Path(bundle_cwd) / dockerfile), "-t", tag, str(bundle_cwd)]


def build_run_argv(
    ls: LaunchServer,
    *,
    image: str,
    container_name: str,
    host_port: int,
    bundle_cwd: str,
) -> list[str]:
    """The hardened `docker run` argv for the sandboxed server.

    Drops all capabilities, forbids privilege escalation, runs read-only and non-root with CPU /
    memory / pid caps, bind-mounts the bundle read-only, and publishes the in-container `port` to a
    loopback host port only — so the sandbox never widens the host's exposure.
    """
    argv = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp",  # noqa: S108 — scratch inside the read-only container, not a host temp path
        "--user",
        _USER,
        "--cpus",
        _CPUS,
        "--memory",
        _MEMORY,
        "--pids-limit",
        _PIDS_LIMIT,
        "-p",
        f"127.0.0.1:{host_port}:{ls.port}",
        "-v",
        f"{bundle_cwd}:{_WORKDIR}:ro",
        "-w",
        _WORKDIR,
    ]
    for key, value in ls.env.items():
        argv += ["-e", f"{key}={value}"]
    argv.append(image)
    argv += shlex.split(ls.cmd)
    return argv


def resolve_image(
    ls: LaunchServer, *, exec_fn: ExecFn, bundle_cwd: str, tag: str
) -> tuple[str, dict[str, str | None]]:
    """Resolve the container image, requiring exactly one of `dockerImage` / `dockerfile`.

    A named `dockerImage` is used as-is; a `dockerfile` is built into `tag` via `exec_fn`. With
    neither or both, sandbox cannot proceed and fails loud (never a bare-host fallback).

    Returns:
        The image reference to run, and a provenance record naming how it was obtained.
    """
    has_image = bool(ls.docker_image)
    has_dockerfile = bool(ls.dockerfile)
    if has_image == has_dockerfile:
        raise SandboxError(
            "sandbox launchServer needs exactly one of dockerImage or dockerfile "
            f"(got {'both' if has_image else 'neither'})"
        )
    if ls.docker_image is not None:
        return ls.docker_image, {"source": "dockerImage", "image": ls.docker_image}
    assert ls.dockerfile is not None  # the exactly-one check above guarantees this
    # The dockerfile comes from the (untrusted) uploaded config; confine it to the bundle so it can't
    # read a Dockerfile elsewhere on the host (`/etc/...`, `../secrets`).
    df = Path(ls.dockerfile)
    if df.is_absolute() or ".." in df.parts:
        raise SandboxError(
            f"dockerfile must be a bundle-relative path without '..': {ls.dockerfile!r}"
        )
    exec_fn(build_image_argv(ls.dockerfile, bundle_cwd, tag))
    return tag, {"source": "dockerfile", "image": tag}


def start_sandboxed_server(
    eff: Effective,
    *,
    bundle_root: Path | str | None = None,
    run_fn: RunFn = _real_run,
    exec_fn: ExecFn = _real_exec,
    log: Callable[[str], None] | None = None,
) -> tuple[Callable[[], None], dict[str, str | None]]:
    """Bring up `eff.launch_server` in a container, returning `(teardown, decision)`.

    Reuses an externally-answering `readyUrl` (no container), else resolves the image, publishes the
    in-container `port` to `readyUrl`'s loopback host port, runs the hardened container, and waits on
    the readiness probe. Tears the container down and fails loud if it exits early or never serves.
    The caller guarantees `eff.launch_server` is set (the sandbox path is only reached for a target
    that declares one).

    `bundle_root` is the **trusted** host directory bind-mounted read-only into the container and used
    as the `docker build` context — the run's confined working directory (the extracted bundle), which
    `serve` controls. It defaults to the process cwd. It is deliberately *not* taken from the
    (untrusted) config `launchServer.cwd`, so a hostile config cannot bind or build from an arbitrary
    host path.

    Returns:
        A teardown callable (a no-op when an externally-started server is reused), and a provenance
        record of the decision (`reused` / `sandboxed`).
    """
    ls = eff.launch_server
    say = log or _default_log
    assert ls is not None, "start_sandboxed_server requires a launchServer (caller must guard)"
    ready_url = ls.ready_url or eff.base_url
    if not ready_url:
        raise SandboxError("launchServer needs readyUrl (or set the target's baseUrl to probe)")

    if _probe(ready_url):
        say(f"launchServer(sandbox): {ready_url} already serving — reusing it (no container)")
        return (lambda: None), {
            "decision": "reused",
            "field": "launchServer",
            "source": None,
            "image": None,
        }

    if ls.port is None:
        raise SandboxError("sandbox launchServer needs `port` (the in-container listen port)")
    try:
        host_port = urlsplit(ready_url).port
    except ValueError as e:
        raise SandboxError(f"sandbox launchServer: invalid port in {ready_url}") from e
    if host_port is None:
        raise SandboxError(
            f"sandbox launchServer needs a port in baseUrl/readyUrl to publish to ({ready_url})"
        )

    container = _container_name()
    # The trusted, caller-supplied bundle root (never the untrusted config `cwd`), absolute so Docker
    # gets an unambiguous bind source / build context.
    bundle_cwd = str(Path(bundle_root or Path.cwd()).resolve())
    image, meta = resolve_image(
        ls, exec_fn=exec_fn, bundle_cwd=bundle_cwd, tag=f"bajutsu-sandbox:{container}"
    )

    def teardown() -> None:
        # Best-effort, like launch_server._terminate: a container `--rm` already auto-removed (it
        # exited on its own) makes `docker rm -f` fail with "No such container" — that is success
        # for teardown, and must not mask the real "container exited" error on the early-exit path.
        say(f"launchServer(sandbox): stopping container {container}")
        with contextlib.suppress(SandboxError):
            exec_fn(["docker", "rm", "-f", container])

    say(f"launchServer(sandbox): starting container {container} from {image}")
    proc = run_fn(
        build_run_argv(
            ls, image=image, container_name=container, host_port=host_port, bundle_cwd=bundle_cwd
        )
    )
    deadline = time.monotonic() + ls.ready_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            teardown()
            raise SandboxError(
                f"sandbox container exited (code {proc.returncode}) before {ready_url} was ready "
                f"— cmd: {ls.cmd}"
            )
        if _probe(ready_url):
            say(f"launchServer(sandbox): {ready_url} ready")
            return teardown, {"decision": "sandboxed", "field": "launchServer", **meta}
        time.sleep(_POLL_INTERVAL)
    teardown()
    raise SandboxError(
        f"sandbox launchServer: {ready_url} not ready after {ls.ready_timeout}s — cmd: {ls.cmd}"
    )
