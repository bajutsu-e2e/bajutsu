"""Execute one job spec on this machine, with no server behind it (BE-0449).

`bajutsu worker --once <spec>` and the public `bajutsu run` both land here, so the command line and
a leased job run through one implementation: `execute_job_spec`, then the unchanged `run_job`. What
differs from a leased job is only what a server would otherwise supply. Object I/O is local
(`LocalWorkerIO`): no signed URL is fetched and nothing is uploaded, so the run tree stays where the
run wrote it. The child inherits this process's standard streams, so the PASS or FAIL line reaches
the caller's standard output and an interactive `--step` prompt reads the caller's terminal. A
SIGINT, SIGTERM, or SIGHUP becomes the same cancel a leased job receives from its heartbeat
(BE-0370 for `run`), so an interrupted caller never leaves a run driving the device on its own.

Nothing here judges a run: the exit code is the child's, or 128 plus the signal number once the
caller was signalled.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from bajutsu.run import INTERNAL_MODULE
from bajutsu.serve.server.worker_job import CancelRelay, execute_job_spec
from bajutsu.serve.state import Job

_logger = logging.getLogger(__name__)

# The signals that cancel a one-shot job. SIGHUP joins the two the item names because the child runs
# in a session of its own: a closed terminal would otherwise end the caller and leave the run going.
# Looked up by name, so this module imports on Windows, which has no SIGHUP.
CANCEL_SIGNALS: tuple[signal.Signals, ...] = tuple(
    sig
    for sig in (getattr(signal, name, None) for name in ("SIGINT", "SIGTERM", "SIGHUP"))
    if sig is not None
)

# The id a one-shot job carries when its spec names none; it labels log records and nothing else.
_DEFAULT_JOB_ID = "once"


class LocalWorkerIO:
    """The `WorkerIO` of a one-shot job: every operation is local, so each is a no-op.

    A leased job reaches object storage through URLs the server signs (BE-0160). A one-shot job has
    no server: the baselines its `--baselines` names are already on disk (`refusal` turns away a
    spec that asks for them to be fetched), the run tree stays where
    the run wrote it, and a `record` job's authored scenario is already at its `--out` path.
    """

    def download_baselines(self, work: Path) -> None:
        """Leave the baselines on disk as they are; nothing is fetched."""

    def upload_run(self, work: Path, run_id: str) -> None:
        """Leave the run tree under the workspace; nothing is uploaded."""

    def save_scenario(self, work: Path, out_path: str, app: str, ref: str) -> None:
        """Leave the authored scenario at its output path; nothing is persisted elsewhere."""


def refusal(spec: Any, work: Path | None = None) -> str | None:
    """Why *spec* cannot run as a one-shot job in workspace *work*, or None when it can.

    Checked before anything runs. An uploaded bundle, artifact overrides, and visual baselines
    arrive only by signed URL, which a one-shot job has none of; a Device Farm batch needs a
    device-cloud worker, which BE-0448 adds. The materials a server ships are written into the
    workspace, which for a one-shot job is the caller's own directory, so a material that would
    overwrite a different file there is refused rather than written over it; *work* None skips that
    check.
    """
    if not isinstance(spec, dict):
        return "a job spec must be a JSON object"
    if (reason := _needs_a_server(spec)) is not None:
        return reason
    cmd = spec.get("cmd")
    if not (isinstance(cmd, list) and cmd and all(isinstance(arg, str) for arg in cmd)):
        return "the spec needs a `cmd`: a non-empty list of strings"
    if (reason := _shape_problem(spec)) is not None:
        return reason
    udids = spec.get("udids")
    if udids is not None and not (
        isinstance(udids, list) and all(isinstance(u, str) for u in udids)
    ):
        return "the spec's `udids` must be a list of strings"
    materials = spec.get("materials")
    if materials is not None and not (
        isinstance(materials, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in materials.items())
    ):
        return "the spec's `materials` must map relative paths to file contents (strings)"
    escaping = sorted(rel for rel in materials or {} if _escapes(work, rel))
    if escaping:
        # `_materialize` would skip such a path silently, and the job would run without the scenario
        # or config it declares.
        return (
            "the spec's materials must name files inside the workspace: "
            f"{', '.join(escaping)} is absolute, climbs out with `..`, or leaves it through a link"
        )
    if work is not None and materials:
        clashes = sorted(rel for rel, content in materials.items() if _clashes(work, rel, content))
        if clashes:
            return (
                "the spec's materials would overwrite a different file in this directory: "
                f"{', '.join(clashes)} — run it from an empty directory"
            )
    return None


def _needs_a_server(spec: dict[str, Any]) -> str | None:
    """Why *spec* names something only a server supplies, or None when it names nothing of the kind."""
    if spec.get("bundle") is not None:
        return (
            "the spec names an uploaded bundle, which a worker fetches by signed URL; a one-shot "
            "run has no server to sign one — run from the bundle's own directory instead"
        )
    if spec.get("overrides") is not None:
        return (
            "the spec names artifact overrides, which a worker fetches by signed URL; a one-shot "
            "run has no server to sign one — place the artifacts in the workspace instead"
        )
    if spec.get("materialize_baselines"):
        return (
            "the spec asks for visual baselines, which a worker fetches by signed URL; a one-shot "
            "run has no server to sign one — point `--baselines` at a directory on disk instead"
        )
    if spec.get("batch") is not None:
        return (
            "the spec is a Device Farm batch, which needs a device-cloud worker; submitting one "
            "without a server arrives with BE-0448"
        )
    return None


def _shape_problem(spec: dict[str, Any]) -> str | None:
    """Which optional spec field has the wrong type, or None when every one present is well formed."""
    checks: tuple[tuple[str, Callable[[Any], bool], str], ...] = (
        # `Job.build` is the shell command that builds the app, serialized as is by `job_spec`.
        ("build", lambda v: v is None or isinstance(v, str), "a string or null"),
        (
            "env_overlay",
            lambda v: (
                v is None
                or (
                    isinstance(v, dict)
                    and all(isinstance(k, str) and isinstance(x, str) for k, x in v.items())
                )
            ),
            "an object of strings",
        ),
        ("app_path", lambda v: v is None or isinstance(v, str), "a string or null"),
        ("graceful_cancel", lambda v: v is None or isinstance(v, bool), "a boolean"),
        ("actor", lambda v: v is None or isinstance(v, str), "a string or null"),
    )
    for field, ok, shape in checks:
        if not ok(spec.get(field)):
            return f"the spec's `{field}` must be {shape}"
    return None


def _escapes(work: Path | None, rel: str) -> bool:
    """Whether material path *rel* names no file strictly inside *work* (the `_materialize` rule).

    Absolute paths and `..` are refused outright; with a *work*, a path is also resolved, so one
    reaching out through a symbolic link, or naming the workspace itself, is caught too.
    """
    path = Path(rel)
    if not rel or path.is_absolute() or path.drive or ".." in path.parts:
        return True
    if work is None:
        return False
    return work.resolve() not in (work / rel).resolve().parents


def _clashes(work: Path, rel: str, content: str) -> bool:
    """Whether material *rel* would replace something in *work* that differs from *content*."""
    dest = work / rel
    if not dest.exists():
        return False
    try:
        return not dest.is_file() or dest.read_text(encoding="utf-8") != content
    except (OSError, UnicodeDecodeError):
        return True


def local_run_spec(argv: list[str]) -> dict[str, Any]:
    """The job spec the public `bajutsu run` executes: the internal run module with *argv*.

    `udids` and `build` stay empty, so the run acquires its devices and builds its app itself, as
    the command line always has. The run is cancelled gracefully, as a served `run` is (BE-0370).
    """
    return {
        "job_id": "run",
        "cmd": [sys.executable, "-m", INTERNAL_MODULE, *argv],
        "udids": [],
        "build": None,
        "graceful_cancel": True,
    }


def run_spec_once(
    spec: dict[str, Any],
    *,
    worker_config: Path | None,
    apply_worker_config: bool = True,
    keep_caller_path: bool = False,
    cwd: Path | None = None,
    popen: Any = subprocess.Popen,
) -> int:
    """Run *spec* to completion on this machine and return the exit code for the caller.

    Args:
        spec: A job spec of the shape the queue stores (`job_spec`); `refusal` must accept it.
        worker_config: The resolved worker capability file a `run` spawn reads (BE-0450), or None
            for the shipped default.
        apply_worker_config: False leaves the spec's own `--worker-config` as written, for the
            public `bajutsu run`, which passes the caller's flag through untouched.
        keep_caller_path: True spawns the run on the caller's own `PATH`, for the public `bajutsu
            run` alone; `worker --once` leaves it False so its run resolves tools as a leased one.
        cwd: The workspace the job runs in; the current directory when None.
        popen: The process factory, injectable for tests.

    Returns:
        The child's exit code; 128 plus the signal number when the child died of a signal, or when
        this process was signalled; 1 when the job ended with no code at all.

    Raises:
        ValueError: `refusal` turns *spec* away; nothing has run or been written.
    """
    # Enforced here as well as by the CLI, so a caller that skipped `refusal` cannot have the spec's
    # materials written outside the workspace. The public wrapper's own spec always passes.
    if (reason := refusal(spec, cwd or Path.cwd())) is not None:
        raise ValueError(f"a one-shot job spec its caller did not vet: {reason}")
    relay = CancelRelay()
    with cancel_on_signals(relay) as signalled:
        job = execute_job_spec(
            {"job_id": _DEFAULT_JOB_ID, **spec},
            popen=popen,
            cwd=cwd,
            io=LocalWorkerIO(),
            worker_config=worker_config,
            apply_worker_config=apply_worker_config,
            keep_caller_path=keep_caller_path,
            on_job=relay.bind,
            one_shot=True,
        )
    return exit_code(job, signalled())


def exit_code(job: Job, signum: int | None) -> int:
    """The caller's exit code for a finished one-shot *job*, after signal *signum* if any.

    A signal reported as 128 plus its number is the shell's own convention, so a script reads an
    interrupted run the same way it reads a run the signal killed outright.
    """
    if signum is not None:
        return 128 + signum
    code = job.exit_code
    if code is None:
        return 1
    return 128 - code if code < 0 else code


class _Child(Protocol):
    """What the signal handlers need of the job's child: `CancelRelay` provides it."""

    def request(self) -> None:
        """Cancel the job, gracefully for `run` (BE-0370)."""

    def pgid(self) -> int | None:
        """The child's process group, or None before it exists."""

    def kill(self) -> None:
        """End the child's group at once."""


class _SignalRelay:
    """The signal handlers of `cancel_on_signals`, with the helper threads they wake."""

    def __init__(self, child: _Child) -> None:
        self._child = child
        self.received: list[int] = []
        self._wake = threading.Event()
        self._kill_now = threading.Event()
        self._done = threading.Event()
        self._previous: dict[signal.Signals, Any] = {}
        self._helpers = [
            threading.Thread(target=self._relay, args=(self._wake, child.request), daemon=True),
            threading.Thread(target=self._relay, args=(self._kill_now, child.kill), daemon=True),
        ]

    def install(self) -> None:
        """Install every handler, leave an ignored signal ignored, and start the helpers."""
        handlers: dict[signal.Signals, Any] = dict.fromkeys(CANCEL_SIGNALS, self._cancel)
        # Job control exists only on POSIX; elsewhere the two are absent and nothing is relayed.
        for name, handler in (("SIGTSTP", self._stop), ("SIGCONT", self._continue)):
            if (sig := getattr(signal, name, None)) is not None:
                handlers[sig] = handler
        try:
            for sig, handler in handlers.items():
                # `nohup` ignores SIGHUP, and a shell ignores SIGINT for a background job, so the run
                # they started must outlive both.
                if signal.getsignal(sig) != signal.SIG_IGN:
                    self._previous[sig] = signal.signal(sig, handler)
        except ValueError as exc:
            _logger.warning("cannot install the cancel handlers (%s); signals keep defaults", exc)
            self._restore()
        for helper in self._helpers:
            helper.start()

    def uninstall(self) -> None:
        """Put the previous handlers back and end the helpers, letting a pending cancel finish."""
        self._restore()
        if self._wake.is_set():
            self._helpers[0].join(timeout=1.0)
        self._done.set()
        self._wake.set()
        self._kill_now.set()
        for helper in self._helpers:
            helper.join(timeout=1.0)

    def _restore(self) -> None:
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)
        self._previous.clear()

    def _relay(self, event: threading.Event, act: Callable[[], None]) -> None:
        event.wait()
        if not self._done.is_set():
            act()

    def _cancel(self, signum: int, _frame: object) -> None:
        if not self.received:
            self.received.append(signum)
            self._wake.set()
        elif signum == signal.SIGINT:
            self._kill_now.set()

    def _signal_group(self, sig: int) -> None:
        pgid = self._child.pgid()
        if pgid is not None:
            with contextlib.suppress(OSError):
                os.killpg(pgid, sig)

    def _stop(self, _signum: int, _frame: object) -> None:
        self._signal_group(signal.SIGSTOP)
        # Stop this process the way the default disposition would, then pick up where it left off.
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTSTP)
        if not self._done.is_set():  # the block may have ended while this process was stopped
            signal.signal(signal.SIGTSTP, self._stop)
        self._signal_group(signal.SIGCONT)

    def _continue(self, _signum: int, _frame: object) -> None:
        self._signal_group(signal.SIGCONT)


@contextmanager
def cancel_on_signals(child: _Child) -> Iterator[Callable[[], int | None]]:
    """Relay the caller's signals to the job's child while the block runs.

    - The first SIGINT, SIGTERM, or SIGHUP cancels the job once (`child.request`).
    - A second SIGINT ends the child's group at once (`child.kill`): an operator who presses Ctrl-C
      again does not want to wait out the grace window.
    - SIGTSTP (Ctrl-Z) stops the child's group, then this process; SIGCONT continues both. The child
      runs in a session of its own, so the terminal's job control would not otherwise reach it.

    Yields a reader for the first cancelling signal received, or None. The cancel and the kill run
    on helper threads: a cancel takes the job's lock, which the main thread may hold at the moment
    the signal lands. A signal already ignored stays ignored. The previous handlers come back when
    the block ends. When they cannot be installed (not the main thread), the block runs with the
    dispositions it found and the reader stays None.
    """
    relay = _SignalRelay(child)
    relay.install()
    try:
        yield lambda: relay.received[0] if relay.received else None
    finally:
        relay.uninstall()
