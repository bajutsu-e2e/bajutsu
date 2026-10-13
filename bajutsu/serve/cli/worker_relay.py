"""The worker's half of the live relays a `record` and a `crawl` need on a worker (BE-0449 unit 8).

A `crawl` grows its screen map in a file, and the page draws the graph from it while it runs; a
`record` can pause for a person and read the answer from its standard input. On the server's own
process both worked through the shared disk and the child's pipes. On a worker they go over HTTP:
`ArtifactStreamer` posts each new snapshot of the screen map, and `HandoffRelay` posts a paused
`record`'s request and polls for the person's answer, which it writes to the child's input. Both
are best effort and never fail the job: the file the crawl writes stays the durable copy, and a
`record` that hears no answer stops waiting on its own timeout.

Each takes the HTTP post as a callable, so the gate drives them without a network.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bajutsu.common.handoff import DEFAULT_TIMEOUT_SECONDS, REQUEST_LINE_PREFIX
from bajutsu.serve.jobs import send_response
from bajutsu.serve.operations.worker import WORKER_ARTIFACT_MAX_BYTES

if TYPE_CHECKING:
    from collections.abc import Callable

    from bajutsu.serve.server.worker_job import CancelRelay
    from bajutsu.serve.state import Job

_logger = logging.getLogger("bajutsu.worker")

# The screen map a crawl writes into its `--out` directory, which the Crawl tab draws.
SCREENMAP = "screenmap.json"

# How often a paused `record`'s worker asks for the person's answer. The heartbeat's interval is
# far too long for a person waiting on the page, and the poll runs only while a handoff is open.
DEFAULT_HANDOFF_POLL_INTERVAL = 1.0
# How long the worker polls for one answer: the `record`'s own wait plus a margin, so the worker
# never stops before the `record` does.
_HANDOFF_POLL_DEADLINE = DEFAULT_TIMEOUT_SECONDS + 30.0
# Statuses that mean the control plane predates the relay routes, so no later post can succeed.
_ROUTE_ABSENT = frozenset({401, 404, 405})


class HandoffRelay:
    """Relays a paused `record`'s request for a person to the server and the answer back.

    `offer` receives the request line the `record` printed. A thread posts the request to
    ``/api/worker/handoff``, then polls ``/api/worker/handoff-response`` until the answer comes,
    the job is cancelled, the lease is lost, the relay stops, or `_HANDOFF_POLL_DEADLINE` passes.
    The answer goes to the `record`'s standard input, as the server's own process writes it for a
    `record` it runs. A cancel in the poll's answer goes to *cancel*, so a cancel the page sends
    while the `record` waits stops it at once rather than on the next heartbeat. A newer request
    supersedes an older one still polling.
    """

    def __init__(
        self,
        post: Callable[[str, dict[str, Any]], tuple[int | None, Any]],
        *,
        cancel: CancelRelay,
        poll_interval: float = DEFAULT_HANDOFF_POLL_INTERVAL,
        deadline: float = _HANDOFF_POLL_DEADLINE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._post = post
        self._cancel = cancel
        self._poll_interval = poll_interval
        self._deadline = deadline
        self._clock = clock
        self._lock = threading.Lock()
        self._job: Job | None = None
        self._generation = 0
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    def bind(self, job: Job) -> None:
        """Attach the job whose standard input an answer is written to."""
        with self._lock:
            self._job = job

    def offer(self, line: str) -> None:
        """Relay one request line the `record` printed, on a thread of its own.

        Only a bound `record` hands off to a person; a line from any other kind is ignored.
        """
        if not line.startswith(REQUEST_LINE_PREFIX) or self._stop.is_set():
            return
        with self._lock:
            if self._job is None or self._job.kind != "record":
                return
            self._generation += 1
            generation = self._generation
            thread = threading.Thread(
                target=self._relay,
                args=(line[len(REQUEST_LINE_PREFIX) :], generation),
                daemon=True,
            )
            self._threads.append(thread)
        thread.start()

    def stop(self) -> None:
        """End every poll and wait for the threads, once the job has finished."""
        self._stop.set()
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join()

    def _current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation and not self._stop.is_set()

    def _relay(self, request: str, generation: int) -> None:
        code, _body = self._post("/api/worker/handoff", {"request": request})
        if code != 200:
            # The `record` then waits out its own timeout and cancels, which its log says.
            _logger.warning("the server did not take the handoff request (HTTP %s)", code)
            return
        until = self._clock() + self._deadline
        while self._current(generation) and self._clock() < until:
            code, body = self._post("/api/worker/handoff-response", {})
            if code == 409 or code in _ROUTE_ABSENT:
                return
            if code == 200 and isinstance(body, dict):
                if body.get("cancel") is True:
                    self._cancel.request()
                    return
                response = body.get("response")
                if isinstance(response, str):
                    self._answer(response, generation)
                    return
            self._stop.wait(self._poll_interval)

    def _answer(self, response: str, generation: int) -> None:
        with self._lock:
            job = self._job if generation == self._generation else None
        if job is None or not send_response(job, response):
            _logger.warning("the handoff answer arrived after the record stopped waiting")


class ArtifactStreamer:
    """Posts each new snapshot of a crawl's screen map to the server while the crawl runs.

    `bind` finds the file from the job's `--out`; a job of any other kind has none, and the
    streamer stays idle. Every *interval* seconds a thread checks the file's size and modification
    time, and posts its content to ``/api/worker/artifact`` when they changed and the content parses
    as JSON. A snapshot read mid-write fails to parse and is tried again on the next check. A file
    past `WORKER_ARTIFACT_MAX_BYTES` is not posted; the page reads the file when the job ends.
    Posting stops for good on a lost lease (409), a route the control plane lacks, or a
    ``truncated`` answer, which means the job's live budget is spent.
    """

    def __init__(self, post: Callable[[str, dict[str, Any]], tuple[int | None, Any]]) -> None:
        self._post = post
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._seen: tuple[int, int] | None = None
        self._done = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def bind(self, job: Job, work: Path) -> None:
        """Watch the screen map of *job* when it is a crawl; *work* resolves a relative `--out`."""
        if job.kind != "crawl":
            return
        out = _out_dir(job.cmd)
        if out is None:
            return
        base = Path(job.cwd) if job.cwd is not None else work
        with self._lock:
            self._path = (out if out.is_absolute() else base / out) / SCREENMAP

    def start(self, interval: float) -> None:
        """Check the file every *interval* seconds on a background thread until `stop`."""
        self._thread = threading.Thread(target=self._run, args=(interval,), daemon=True)
        self._thread.start()

    def stop(self, *, final: bool) -> None:
        """End the checks, then post the last snapshot when *final* is True."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if final:
            self.check()

    def check(self) -> None:
        """Post the screen map once if it changed since the last post."""
        with self._lock:
            path = self._path
        if path is None or self._done.is_set():
            return
        try:
            stat = path.stat()
        except OSError:
            return  # the crawl has not written it yet
        seen = (stat.st_mtime_ns, stat.st_size)
        if seen == self._seen:
            return
        if stat.st_size > WORKER_ARTIFACT_MAX_BYTES:
            self._seen = seen
            return
        try:
            content = path.read_text(encoding="utf-8")
            json.loads(content)
        except (OSError, UnicodeError, ValueError):
            return  # read mid-write; the next check reads the finished file
        code, body = self._post("/api/worker/artifact", {"name": SCREENMAP, "content": content})
        if code == 200:
            self._seen = seen
            if isinstance(body, dict) and body.get("truncated") is True:
                self._done.set()
        elif code == 409 or code in _ROUTE_ABSENT:
            self._done.set()

    def _run(self, interval: float) -> None:
        while not self._stop.wait(interval):
            self.check()


def _out_dir(cmd: list[str]) -> Path | None:
    """The directory a crawl command line writes into: its last `--out`, or None."""
    value: str | None = None
    for i, arg in enumerate(cmd):
        if arg == "--out" and i + 1 < len(cmd):
            value = cmd[i + 1]
        elif arg.startswith("--out="):
            value = arg.removeprefix("--out=")
    return Path(value) if value else None
