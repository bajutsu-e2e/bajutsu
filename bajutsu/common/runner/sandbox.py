"""Run a target's hook commands inside one throwaway, hardened Docker container (BE-0491).

A worker whose `commandExecution` policy sets `sandbox` for the config's source (BE-0492) routes the
target's hook commands here instead of spawning them on the bare host, so a hostile command reaches
nothing but a disposable container. The container is **one per run**, not one per command: a server a
`beforeRun` script starts has to keep serving until `afterRun`, which a per-command container could
not do. Bajutsu starts it with an idle command of its own in place of the image's default, runs each
entry inside it with `docker exec`, and removes it after `afterRun`.

Docker is reached through two injected seams (`run_fn` for the long-running container, `exec_fn` for
the short `build` / `exec` / `rm` commands), so every decision and argv is testable without a
daemon; only the real `docker run` / `docker build` is exercised outside the gate.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from bajutsu.common.config.schema.hook_command import HookSandbox

# The container runs as a long-lived process we must poll for early exit; the short docker commands
# (`build`, `exec`, `rm`) run to completion. Defaults reach the real daemon; tests inject fakes.
RunFn = Callable[[list[str]], "subprocess.Popen[bytes]"]
ExecFn = Callable[[list[str]], None]

WORKDIR = "/bundle"  # where the config's directory is bind-mounted read-only inside the container
# Where the run's raw hook area is mounted, writable: the `$BAJUTSU_OUTPUT` file each command writes
# and the raw logs Bajutsu copies into the evidence tree with secrets masked.
HOOK_DIR = "/bajutsu-hooks"
# Resource caps for the disposable container. Conservative, documented defaults — a hostile command
# cannot exhaust the host even before the deeper per-tenant isolation of BE-0015 / BE-0016.
_NOBODY = "65534:65534"  # nobody:nogroup — the fallback when the worker itself runs as root
_CPUS = "1.0"
_MEMORY = "512m"
_PIDS_LIMIT = "256"
# The container's own command, in place of the image's default: it must outlive every entry without
# doing anything. The loop is POSIX `sh`, so it works on a BusyBox image as well as a GNU one
# (`sleep infinity` does not).
IDLE_COMMAND = ("sh", "-c", "while :; do sleep 86400; done")
_READY_TIMEOUT = 30.0  # seconds to wait for the container to accept its first `docker exec`
_POLL_INTERVAL = 0.25  # the grain of that condition wait


class SandboxError(RuntimeError):
    """A sandboxed hook could not run. Raised loudly — never a fallback to the bare host."""


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


def container_user() -> str:
    """The `--user` the container runs as: the worker's own account, never root.

    The raw area's output files are the worker's, owner-only, and the container has to write the one
    belonging to the entry it runs — so it runs as that same user rather than as `nobody`, which
    could not open the file at all. A worker running as root falls back to `nobody`: root in the
    container is the thing this flag exists to prevent, and a root worker is already outside what
    this hardening can protect.
    """
    uid, gid = os.getuid(), os.getgid()
    return _NOBODY if uid == 0 else f"{uid}:{gid}"


def grant_container_access(path: Path, *, root: Path | None = None) -> None:
    """Let the container's user reach and write *path* in the mounted half of the raw area.

    When the worker runs as root the container cannot run as that user — root inside the container
    is what `--user` exists to prevent — so it runs as `nobody` instead and cannot open a
    root-owned, owner-only file. Only root reaches this branch, and only root can hand the
    directory over, so the owner is changed rather than the mode widened: the files stay
    owner-only, with the container's own user as that owner.

    Every directory on the way to the file is handed over too, up to *root* when given: a file the
    container owns is still out of reach behind a root-owned `0700` directory, and the per-entry
    directories are created after the container has started.

    A no-op for every non-root worker, where the container already runs as the file's owner.
    """
    if os.getuid() != 0:
        return
    uid, gid = (int(part) for part in _NOBODY.split(":"))
    targets = [path, *(path.rglob("*") if path.is_dir() else [])]
    # The parents, from the file up to (and including) the mounted root, so the container can
    # traverse to what it owns.
    parent = path.parent
    stop = (root or path).resolve()
    while True:
        targets.append(parent)
        if parent.resolve() == stop or parent == parent.parent:
            break
        parent = parent.parent
    for target in targets:
        with contextlib.suppress(OSError):
            os.chown(target, uid, gid)


def _container_name() -> str:
    return f"bajutsu-sandbox-{uuid.uuid4().hex[:12]}"


def build_image_argv(dockerfile: str, bundle_cwd: str, tag: str) -> list[str]:
    """`docker build` argv that builds the config's `dockerfile` into `tag` (context = that dir)."""
    return ["docker", "build", "-f", str(Path(bundle_cwd) / dockerfile), "-t", tag, str(bundle_cwd)]


def resolve_image(
    sandbox: HookSandbox, *, exec_fn: ExecFn, bundle_cwd: str, tag: str
) -> tuple[str, dict[str, str | None]]:
    """Resolve the container image, requiring exactly one of `dockerImage` / `dockerfile`.

    A named `dockerImage` is used as-is; a `dockerfile` is built into `tag` via `exec_fn`. The
    schema already admits exactly one, so this re-checks only what a hand-built `HookSandbox` could
    still get wrong, and fails loud either way (never a bare-host fallback).

    Returns:
        The image reference to run, and a provenance record naming how it was obtained.
    """
    if (sandbox.docker_image is None) == (sandbox.dockerfile is None):
        both = sandbox.docker_image is not None
        raise SandboxError(
            "hooks.sandbox needs exactly one of dockerImage or dockerfile "
            f"(got {'both' if both else 'neither'})"
        )
    if sandbox.docker_image is not None:
        return sandbox.docker_image, {"source": "dockerImage", "image": sandbox.docker_image}
    assert sandbox.dockerfile is not None  # the exactly-one check above guarantees this
    # The dockerfile comes from the (untrusted) config, so confine it to the directory the worker
    # prepared: it must not read a Dockerfile elsewhere on the host (`/etc/...`, `../secrets`).
    df = Path(sandbox.dockerfile)
    if df.is_absolute() or ".." in df.parts:
        raise SandboxError(
            f"dockerfile must be a path inside the config's directory, without '..': "
            f"{sandbox.dockerfile!r}"
        )
    exec_fn(build_image_argv(sandbox.dockerfile, bundle_cwd, tag))
    return tag, {"source": "dockerfile", "image": tag}


def build_run_argv(
    sandbox: HookSandbox,
    *,
    image: str,
    container_name: str,
    bundle_cwd: str,
    raw_dir: str,
) -> list[str]:
    """The hardened `docker run` argv for the per-run hook container.

    Drops all capabilities, forbids privilege escalation, runs read-only and non-root with CPU /
    memory / pid caps, bind-mounts the config's directory read-only and the run's raw hook area
    writable, and publishes each declared port to the worker's loopback interface only — so the
    sandbox never widens the host's exposure. The image's own default command is replaced by an idle
    loop, since the container exists to be `exec`-ed into rather than to run the image's entrypoint.
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
        container_user(),
        "--cpus",
        _CPUS,
        "--memory",
        _MEMORY,
        "--pids-limit",
        _PIDS_LIMIT,
    ]
    for port in sandbox.ports:
        argv += ["-p", f"127.0.0.1:{port}:{port}"]
    argv += [
        "-v",
        f"{bundle_cwd}:{WORKDIR}:ro",
        "-v",
        f"{raw_dir}:{HOOK_DIR}",
        "-w",
        WORKDIR,
        image,
        *IDLE_COMMAND,
    ]
    return argv


def build_exec_argv(
    container_name: str, argv: list[str], *, env: dict[str, str], cwd: str | None
) -> list[str]:
    """The `docker exec` argv that runs one hook entry inside the already-running container.

    The entry's own environment is passed with `-e`, and its `cwd` is resolved inside the container
    rather than on the host, which the container cannot see.
    """
    out = ["docker", "exec"]
    for key, value in sorted(env.items()):
        out += ["-e", f"{key}={value}"]
    if cwd is not None:
        out += ["-w", cwd]
    out.append(container_name)
    return out + argv


@dataclass
class SandboxRuntime:
    """One target's per-run hook container: started before `beforeRun`, removed after `afterRun`.

    `wrap` turns one entry's host-side argv and environment into the `docker exec` that runs it
    inside the container, with the paths rewritten to what the container can see: `$BAJUTSU_OUTPUT`
    points into the mounted raw area, and `BAJUTSU_RUN_DIR` is dropped, since the run directory is
    not mounted at all.
    """

    sandbox: HookSandbox
    bundle_cwd: Path
    # The *writable half* of the run's raw area (`<raw>/out`), the only part a command inside the
    # container can reach. The logs live in the other half, which is never mounted: under `sandbox`
    # the host captures the `docker exec` client's output, so the container writes no log itself.
    raw_dir: Path
    run_fn: RunFn = _real_run
    exec_fn: ExecFn = _real_exec
    log: Callable[[str], None] | None = None
    container: str = field(default_factory=_container_name)
    _proc: subprocess.Popen[bytes] | None = field(default=None, repr=False)

    def start(self) -> None:
        """Build or pull the image, run the container, and wait until it accepts a command.

        Raises:
            SandboxError: The image cannot be resolved, the container exited early, or it never
                became ready within the bounded wait.
        """
        say = self.log or (lambda _m: None)
        image, _ = resolve_image(
            self.sandbox,
            exec_fn=self.exec_fn,
            bundle_cwd=str(self.bundle_cwd),
            tag=f"bajutsu-sandbox:{self.container}",
        )
        say(f"hooks(sandbox): starting container {self.container} from {image}")
        self._proc = self.run_fn(
            build_run_argv(
                self.sandbox,
                image=image,
                container_name=self.container,
                bundle_cwd=str(self.bundle_cwd),
                raw_dir=str(self.raw_dir),
            )
        )
        deadline = time.monotonic() + _READY_TIMEOUT
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise SandboxError(
                    f"hooks sandbox container exited (code {self._proc.returncode}) before it was "
                    f"ready — image {image!r}"
                )
            try:
                self.exec_fn(["docker", "exec", self.container, "true"])
            except SandboxError:
                time.sleep(_POLL_INTERVAL)
                continue
            return
        self.stop()
        raise SandboxError(
            f"hooks sandbox container {self.container} was not ready after {_READY_TIMEOUT:g}s"
        )

    def wrap(
        self, argv: list[str], env: dict[str, str], cwd: Path | None
    ) -> tuple[list[str], dict[str, str]]:
        """This entry's argv and environment, rewritten to run inside the container."""
        inside = dict(env)
        inside.pop("BAJUTSU_RUN_DIR", None)
        if (out := inside.get("BAJUTSU_OUTPUT")) is not None:
            inside["BAJUTSU_OUTPUT"] = self.in_container(Path(out))
        # The host's own environment means nothing inside the container, so only the variables
        # Bajutsu and the entry set are passed through.
        passed = {k: v for k, v in inside.items() if k.startswith("BAJUTSU_")}
        passed.update({k: v for k, v in env.items() if not k.startswith("BAJUTSU_")})
        container_cwd = self.in_bundle(cwd) if cwd is not None else None
        return build_exec_argv(self.container, argv, env=passed, cwd=container_cwd), {}

    def in_container(self, host_path: Path) -> str:
        """A path inside the mounted raw area, as the container sees it."""
        return f"{HOOK_DIR}/{Path(host_path).relative_to(self.raw_dir).as_posix()}"

    def in_bundle(self, host_path: Path) -> str:
        """A `cwd` inside the mounted config directory, as the container sees it.

        A path outside that directory has no counterpart in the container, so the container's own
        working directory is used rather than a host path it cannot reach.
        """
        try:
            rel = Path(host_path).resolve().relative_to(self.bundle_cwd.resolve())
        except ValueError:
            return WORKDIR
        return WORKDIR if rel == Path() else f"{WORKDIR}/{rel.as_posix()}"

    def stop(self) -> None:
        """Remove the container, best-effort.

        This is also what stops a process an expired `timeout` left running *inside* the container:
        the timeout stops the host-side `docker exec` client, not the command it started, so the
        in-container process lives until the container goes — which is here, after `afterRun`.

        A container `--rm` already auto-removed (it exited on its own) makes `docker rm -f` fail with
        "No such container" — that is success for teardown, and must not mask the real error on the
        early-exit path.
        """
        say = self.log or (lambda _m: None)
        say(f"hooks(sandbox): stopping container {self.container}")
        with contextlib.suppress(SandboxError):
            self.exec_fn(["docker", "rm", "-f", self.container])


def sandbox_command_line(cmd: str) -> list[str]:
    """A hook entry's `cmd` split the way the host path splits it, for the container to run.

    Kept beside the container so both paths read one rule: the command is split like a shell would
    and then run without one, so a pipe or a redirect needs `sh -c` or a script either way.
    """
    return shlex.split(cmd)
