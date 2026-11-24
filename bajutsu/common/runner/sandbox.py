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
import os as os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from bajutsu.common.config.schema.hook_command import HookSandbox

# The container runs as a long-lived process we must poll for early exit; the short docker commands
# (`build`, `exec`, `rm`) run to completion. Defaults reach the real daemon; tests inject fakes.
RunFn = Callable[..., "subprocess.Popen[bytes]"]
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


def _real_run(argv: list[str], log: Path | None = None) -> subprocess.Popen[bytes]:
    """Start the container, keeping docker's own output where a failure can be read from it.

    Written to a file rather than a pipe: the container outlives this call, and a pipe it inherited
    would keep the reader waiting. Without a log path the output is dropped, which only a caller
    that has nowhere to put it should do.
    """
    if log is None:
        return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sink = log.open("ab")
    return subprocess.Popen(argv, stdout=sink, stderr=subprocess.STDOUT)


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

    With *root*, the directories between *path* and *root* are handed over too, that root included:
    a file the container owns is still out of reach behind a root-owned `0700` directory, and the
    per-entry directories are created after the container has started. Without *root*, *path* alone
    is handed over — never its parents, which would walk out of the raw area and up to the
    filesystem root.

    A no-op for every non-root worker, where the container already runs as the file's owner.

    Raises:
        SandboxError: An owner could not be changed, which would leave the container unable to
            write the output file it is handed.
    """
    if os.getuid() != 0:
        return
    uid, gid = (int(part) for part in _NOBODY.split(":"))
    targets = [path, *(sorted(path.rglob("*")) if path.is_dir() else [])]
    if root is not None:
        stop = root.resolve()
        parent = path.parent
        while True:
            targets.append(parent)
            if parent.resolve() == stop or parent == parent.parent:
                break
            parent = parent.parent
    for target in targets:
        try:
            os.chown(target, uid, gid)
        except OSError as e:
            raise SandboxError(
                f"could not hand {target} to the container's user ({uid}:{gid}): {e} — a sandboxed "
                "command could not write its $BAJUTSU_OUTPUT there"
            ) from e


def _default_log(msg: str) -> None:
    """Where a runtime with no logger of its own says what it did: the operator's terminal."""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


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
    # Where docker's own output goes, so a container that cannot start says why. None drops it.
    docker_log: Path | None = None
    # The tag `resolve_image` built from a `dockerfile`, which `stop` removes; a named `dockerImage`
    # is the operator's own and is left alone.
    built_tag: str | None = field(default=None, repr=False)
    _proc: subprocess.Popen[bytes] | None = field(default=None, repr=False)

    def start(self) -> None:
        """Build or pull the image, run the container, and wait until it accepts a command.

        Raises:
            SandboxError: The image cannot be resolved, the container exited early, or it never
                became ready within the bounded wait.
        """
        say = self.log or (lambda _m: None)
        tag = f"bajutsu-sandbox:{self.container}"
        image, record = resolve_image(
            self.sandbox, exec_fn=self.exec_fn, bundle_cwd=str(self.bundle_cwd), tag=tag
        )
        if record.get("source") == "dockerfile":
            self.built_tag = tag
        say(f"hooks(sandbox): starting container {self.container} from {image}")
        self._proc = self.run_fn(
            build_run_argv(
                self.sandbox,
                image=image,
                container_name=self.container,
                bundle_cwd=str(self.bundle_cwd),
                raw_dir=str(self.raw_dir),
            ),
            self.docker_log,
        )
        deadline = time.monotonic() + _READY_TIMEOUT
        # The last probe refusal, kept rather than dropped: "the container is not up yet" and "the
        # docker daemon refuses this user" look the same here, and reporting a readiness timeout
        # for the second sends the operator looking in the wrong place.
        last: SandboxError | None = None
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise SandboxError(
                    f"hooks sandbox container exited (code {self._proc.returncode}) before it was "
                    f"ready — image {image!r}{self._docker_said()}"
                )
            try:
                self.exec_fn(["docker", "exec", self.container, "true"])
            except SandboxError as e:
                last = e
                time.sleep(_POLL_INTERVAL)
                continue
            return
        self.stop()
        said = f"; the last `docker exec` said: {last}" if last is not None else ""
        raise SandboxError(
            f"hooks sandbox container {self.container} was not ready after {_READY_TIMEOUT:g}s"
            f"{said}{self._docker_said()}"
        )

    def _docker_said(self) -> str:
        """The tail of docker's own output, or an empty string when there is none to read.

        Exit 125 is docker's "the run could not start", and the sentence explaining it — a port
        already allocated, no such image, an invalid mount — is the one thing the operator needs.
        """
        if self.docker_log is None:
            return ""
        try:
            text = self.docker_log.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        return f" — docker said: {text[-2000:]}" if text else ""

    def wrap(
        self,
        argv: list[str],
        bajutsu_env: Mapping[str, str],
        declared: Mapping[str, str],
        cwd: Path | None,
    ) -> list[str]:
        """This entry's argv, rewritten to run inside the container.

        Two environments go in, and nothing else: *bajutsu_env* is the `BAJUTSU_*` set Bajutsu
        built for this entry, and *declared* is the `env` the config wrote on it. The worker's own
        environment is deliberately absent. The container holds a command the worker does not
        trust, and the worker's process carries its own credentials — handing those over would
        undo what `--cap-drop=ALL`, `--read-only` and `--user` are for.
        """
        inside = {k: v for k, v in bajutsu_env.items() if k.startswith("BAJUTSU_")}
        inside.pop("BAJUTSU_RUN_DIR", None)  # the run directory has no mount inside the container
        if (out := inside.get("BAJUTSU_OUTPUT")) is not None:
            inside["BAJUTSU_OUTPUT"] = self.in_container(Path(out))
        if (source := inside.get("BAJUTSU_SCENARIO_FILE")) is not None:
            # Dropped, like the run directory, when the container mounts no such file.
            if Path(source).resolve().is_relative_to(self.bundle_cwd.resolve()):
                inside["BAJUTSU_SCENARIO_FILE"] = self.in_bundle(Path(source))
            else:
                del inside["BAJUTSU_SCENARIO_FILE"]
        container_cwd = self.in_bundle(cwd) if cwd is not None else None
        return build_exec_argv(self.container, argv, env={**inside, **declared}, cwd=container_cwd)

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
            (self.log or _default_log)(
                f"hooks(sandbox): cwd {host_path} lies outside the mounted config directory; "
                f"the command runs in {WORKDIR} instead"
            )
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
        say = self.log or _default_log
        say(f"hooks(sandbox): stopping container {self.container}")
        try:
            self.exec_fn(["docker", "rm", "-f", self.container])
        except SandboxError as e:
            if "No such container" not in str(e):
                # Every other cause leaves the container running and holding its published ports,
                # which fails every later run of this config at bring-up. Saying so beats a clean
                # teardown report that is not true.
                say(
                    f"hooks(sandbox): could not remove container {self.container}: {e} — it may "
                    f"still be running and holding its published port(s); remove it with "
                    f"`docker rm -f {self.container}`"
                )
        if self.built_tag is not None:
            # An image built from the config's `dockerfile` is tagged per run, so leaving it behind
            # would add one image per run to the worker's disk.
            with contextlib.suppress(SandboxError):
                self.exec_fn(["docker", "image", "rm", "-f", self.built_tag])
