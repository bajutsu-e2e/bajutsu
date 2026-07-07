"""Live-log Server-Sent Events serve operations (BE-0127)."""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator

from bajutsu.handoff import REQUEST_LINE_PREFIX
from bajutsu.serve.jobs import Job, ServeState


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
    A generator so the caller can ``close()`` it to stop the underlying stream on a disconnect."""
    job = state.jobs.get(job_id)
    if job is None:
        return None
    return _job_sse_frames(state, job, job_id, keepalive)


def _job_sse_frames(state: ServeState, job: Job, job_id: str, keepalive: float) -> Generator[str]:
    for line in state.logbus.stream(job_id, timeout=keepalive):
        yield ":keepalive\n\n" if line is None else format_sse(*_classify(line))
    yield format_sse("done", _terminal_payload(state, job, job_id))
