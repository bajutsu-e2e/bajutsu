"""Live-log Server-Sent Events serve operations (BE-0127)."""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator

from bajutsu.common.handoff import REQUEST_LINE_PREFIX
from bajutsu.serve.operations._common import no_worker_notice
from bajutsu.serve.state import Job, ServeState


def _classify(line: str) -> tuple[str, str]:
    """A bus line as an ``(event, data)`` pair: a handoff request (BE-0179) becomes a
    ``human-request`` event carrying just its JSON payload; every other line is a ``log``."""
    if line.startswith(REQUEST_LINE_PREFIX):
        return ("human-request", line[len(REQUEST_LINE_PREFIX) :])
    return ("log", line)


def format_sse(event: str, data: str) -> str:
    """One Server-Sent Event frame. *data* is split on line breaks into one ``data:`` line each,
    ended by a single blank line so the browser dispatches it. Splitting matters: a value with an
    embedded newline (a multi-line or crafted log line) would otherwise inject extra SSE fields
    (e.g. a fake ``event:``), and a LogBus line's trailing newline would add a stray blank line."""
    body = "".join(f"data: {line}\n" for line in data.splitlines()) or "data: \n"
    return f"event: {event}\n{body}\n"


def job_log_events(state: ServeState, job_id: str) -> Iterator[tuple[str, str]] | None:
    """The live-log stream for *job_id* as ``(event, data)`` pairs — a ``log`` per line (backlog +
    live from the LogBus), then a terminal ``done`` carrying the job's final view — or None if the
    job is unknown. The buffered bus means a subscriber that attaches after the job finished still
    replays everything. The blocking iteration is the caller's to host (a thread per request)."""
    job = state.jobs.get(job_id)
    if job is None:
        return None
    return _job_event_pairs(state, job, job_id)


def _job_event_pairs(state: ServeState, job: Job, job_id: str) -> Iterator[tuple[str, str]]:
    for line in state.logbus.stream(job_id):
        if line is not None:  # no timeout passed, so no heartbeats — guard only satisfies the type
            yield _classify(line)
    yield ("done", _terminal_payload(state, job, job_id))


def _terminal_payload(state: ServeState, job: Job, job_id: str) -> str:
    """The job's terminal status (JSON): the worker-recorded view on the bus (server backend), else
    the local Job's (BE-0015 W2). The stream has ended, so `close` ran and any final payload is set.
    Lines are omitted — they already arrived as `log` events, so the done payload needn't repeat
    them."""
    final = state.logbus.final(job_id)
    return final if final is not None else json.dumps(job.view(include_lines=False))


def job_sse(state: ServeState, job_id: str, *, keepalive: float) -> Generator[str] | None:
    """The job's log as ready-to-send SSE strings — `log` frames, a terminal `done` frame, and a
    ``:keepalive`` comment whenever the stream is idle for *keepalive* seconds (so a reverse proxy
    won't drop the connection during a quiet stretch) — or None if the job is unknown (BE-0015).
    A `notice` frame says when a queued job has no live worker, and again when that ends (BE-0449).
    A generator so the caller can ``close()`` it to stop the underlying stream on a disconnect."""
    job = state.jobs.get(job_id)
    if job is None:
        return None
    return _job_sse_frames(state, job, job_id, keepalive)


def _job_sse_frames(state: ServeState, job: Job, job_id: str, keepalive: float) -> Generator[str]:
    # The no-worker notice (BE-0449) is sent when the stream opens and re-read on every idle beat,
    # and a `notice` event carries each change, so the page shows it while the job waits and drops
    # it once a worker takes the job. A line arriving means a worker is running it, so it clears too.
    notice = no_worker_notice(state, job)
    if notice is not None:
        yield _notice_frame(notice)
    for line in state.logbus.stream(job_id, timeout=keepalive):
        if line is None:
            current = no_worker_notice(state, job)
            if current != notice:
                notice = current
                yield _notice_frame(notice)
            yield ":keepalive\n\n"
            continue
        if notice is not None:
            notice = None
            yield _notice_frame(None)
        yield format_sse(*_classify(line))
    yield format_sse("done", _terminal_payload(state, job, job_id))


def _notice_frame(notice: str | None) -> str:
    """A ``notice`` event: the job's current notice, or null once it no longer applies."""
    return format_sse("notice", json.dumps({"notice": notice}))
