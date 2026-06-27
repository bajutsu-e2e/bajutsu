"""Operational logging for the hosted serve (BE-0055).

The tool's *own* diagnostic trace — structured (JSON), correlated by ids, and redacted —
deliberately distinct from evidence, the run output stream, and the audit log. It is a
**serve-mode** concern: `configure` is called once at serve startup; the deterministic
`run` / CI gate never imports this module, so that path stays stdlib-only and quiet.

`configure` takes over the root logger as the process's sole sink, so redaction and correlation
cover every record that reaches it (including third-party loggers that propagate to root) without
each call site having to cooperate. A child logger that installs its own non-propagating handler
is its own sink and outside this guarantee.

- **redaction**: known secret *values* are masked on the fully rendered line (`_mask_values`,
  reusing the shared `Redactor` from `redaction.py`), so a secret is scrubbed wherever it lands —
  even inside a non-string field. Sensitive field *names* (`_NameMaskFilter`) are masked
  structurally before serialization. A process-lifetime redactor (serve token, OAuth secret, API
  key) is seeded at `configure`; a run-scoped one (a run's resolved `${secrets.X}`) can be bound
  per run via a `contextvar` wherever those values are in-process.
- **correlation** (`_ContextFilter`): `request_id` / `org` / `actor` / `job_id` / `run_id`
  held in `contextvars` and injected into every record. Cross-process correlation is by shared
  id *value* (the ids already travel in the job spec / are the run's own id), never by
  propagating a context object.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import IO, Any

from bajutsu.redaction import PLACEHOLDER, Redactor

# Stable event names so an SRE can grep/alert on `event=`. Keep additions deliberate.
EVENTS: frozenset[str] = frozenset(
    {
        "run.dispatched",
        "run.recorded",
        "oauth.login",
        "quota.rejected",
        "worker.job.started",
        "worker.job.finished",
        "artifact.upload.failed",
    }
)

# Sensitive structured-field *names*, masked by name regardless of value (substring, case-folded).
_SENSITIVE_KEYS: tuple[str, ...] = (
    "authorization",
    "token",
    "secret",
    "password",
    "cookie",
    "api_key",
)

# Correlation ids, carried out-of-band so they need not thread through every signature.
_request_id: ContextVar[str | None] = ContextVar("bajutsu_request_id", default=None)
_org: ContextVar[str | None] = ContextVar("bajutsu_org", default=None)
_actor: ContextVar[str | None] = ContextVar("bajutsu_actor", default=None)
_job_id: ContextVar[str | None] = ContextVar("bajutsu_job_id", default=None)
_run_id: ContextVar[str | None] = ContextVar("bajutsu_run_id", default=None)
_run_redactor: ContextVar[Redactor | None] = ContextVar("bajutsu_run_redactor", default=None)

# The schema's correlation keys, rendered explicitly (and so excluded from the generic extras).
_CONTEXT_KEYS: tuple[tuple[str, ContextVar[str | None]], ...] = (
    ("request_id", _request_id),
    ("org", _org),
    ("actor", _actor),
    ("job_id", _job_id),
    ("run_id", _run_id),
)

# LogRecord attributes present on a bare record — anything else a caller attached via `extra`.
_RESERVED: frozenset[str] = frozenset(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "event",
}


def new_request_id() -> str:
    """A short, unique id to mint at a request boundary."""
    return uuid.uuid4().hex


def _is_sensitive_key(key: str) -> bool:
    low = key.lower()
    return any(word in low for word in _SENSITIVE_KEYS)


_SCHEMA_KEYS: frozenset[str] = _RESERVED | {key for key, _ in _CONTEXT_KEYS}


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """The structured fields a caller attached via ``extra=`` (excluding the schema's own keys)."""
    return {
        k: v for k, v in record.__dict__.items() if k not in _SCHEMA_KEYS and not k.startswith("_")
    }


class _ContextFilter(logging.Filter):
    """Fill the correlation ids from the bound context, without clobbering explicit values.

    A caller can name an id directly (e.g. ``log_event(..., org=...)`` on the control plane, which
    binds nothing in context); the contextvar only fills the ids the caller left unset (the common
    case — the request / worker boundary bound them out-of-band).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for key, var in _CONTEXT_KEYS:
            if getattr(record, key, None) is None:
                setattr(record, key, var.get())
        return True


class _NameMaskFilter(logging.Filter):
    """Mask sensitive structured fields by *name* (`authorization`, `token`, …) to ``[REDACTED]``.

    Runs as a handler filter so it covers every record this handler writes, including ones
    propagated from child loggers. Value-based masking happens later, on the fully rendered line
    (`_mask_values`), so a secret carried by a non-string field is caught there too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for key in _extras(record):
            if _is_sensitive_key(key):
                setattr(record, key, PLACEHOLDER)
        return True


def _mask_values(static: Redactor, text: str) -> str:
    """Mask known secret *values* in a fully rendered line: process-static, then run-scoped."""
    text = static.redact_text(text)
    run = _run_redactor.get()
    if run is not None:
        text = run.redact_text(text)
    return text


class _JsonFormatter(logging.Formatter):
    """Single-line JSON: ``ts, level, logger, event?, msg, <correlation ids?>, <extras?>``.

    Value-masking is applied to the whole serialized line, so a known secret value is scrubbed
    wherever it lands, including inside a non-string field stringified by ``json.dumps``.
    """

    def __init__(self, static: Redactor) -> None:
        super().__init__()
        self._static = static

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        event = getattr(record, "event", None)
        if event is not None:
            line["event"] = event
        line["msg"] = record.getMessage()
        for key, _ in _CONTEXT_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                line[key] = value
        line.update(
            _extras(record)
        )  # caller-attached fields keep their (deterministic) insert order
        return _mask_values(self._static, json.dumps(line, ensure_ascii=False, default=str))


class _TextFormatter(logging.Formatter):
    """Human-readable single line for the CLI, value-masked like the JSON channel."""

    def __init__(self, static: Redactor) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self._static = static

    def format(self, record: logging.LogRecord) -> str:
        return _mask_values(self._static, super().format(record))


def make_handler(
    *, stream: IO[str] | None = None, fmt: str = "json", secrets: tuple[str, ...] = ()
) -> logging.Handler:
    """A stdout (or *stream*) handler wired with the correlation + redaction filters.

    Args:
        stream: Sink; defaults to ``sys.stdout`` (the 12-factor contract).
        fmt: ``json`` for the structured serve channel, ``text`` for the human-readable CLI.
        secrets: Process-lifetime secret values to mask wherever they appear.
    """
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    static = Redactor(None, values=list(secrets))
    handler.setFormatter(_JsonFormatter(static) if fmt == "json" else _TextFormatter(static))
    handler.addFilter(_ContextFilter())
    handler.addFilter(_NameMaskFilter())
    handler._bajutsu_oplog = True  # type: ignore[attr-defined]  # marks ours, so reset() finds it
    return handler


def configure(
    *,
    fmt: str = "json",
    level: str = "INFO",
    secrets: tuple[str, ...] = (),
    stream: IO[str] | None = None,
) -> None:
    """Install the operational-logging handler as the root logger's sole sink (serve startup only).

    Takes over the root logger: any handler already installed is removed first, so no sibling
    handler can write an unredacted line past this one. A child logger that has its own
    non-propagating handler is that handler's concern, not this sink's.
    """
    if level.upper() not in logging.getLevelNamesMapping():
        raise ValueError(
            "BAJUTSU_LOG_LEVEL must be a standard level name "
            f"(DEBUG/INFO/WARNING/ERROR/CRITICAL), got {level!r}"
        )
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level.upper())
    root.addHandler(make_handler(stream=stream, fmt=fmt, secrets=secrets))


def reset() -> None:
    """Remove our handler(s) from the root logger — idempotent reconfigure / test teardown."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_bajutsu_oplog", False):
            root.removeHandler(handler)


def log_event(
    logger: logging.Logger, event: str, msg: str = "", *, level: int = logging.INFO, **fields: Any
) -> None:
    """Emit *msg* tagged with a stable ``event`` name (and any extra structured *fields*)."""
    logger.log(level, msg, extra={"event": event, **fields})


def bind_request(request_id: str) -> None:
    """Bind *request_id* at a request boundary without scoping it.

    Sound for a server that gives each request its own context: the stdlib ``ThreadingHTTPServer``
    (one thread per request) and an ASGI server (one asyncio ``Task`` per request) both bind on a
    per-request context copy. The binding is overwritten at the next request's entry rather than
    explicitly reset, which is why every ``do_GET`` / ``do_POST`` and the middleware rebind first
    thing. Use ``request_context`` where one context is reused across calls and a reset is needed.
    """
    _request_id.set(request_id)


@contextmanager
def request_context(request_id: str) -> Iterator[None]:
    """Bind *request_id* for the duration of one request."""
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


@contextmanager
def job_context(*, job_id: str, org: str | None = None, actor: str | None = None) -> Iterator[None]:
    """Bind a worker job's ids while it runs."""
    job_tok, org_tok, actor_tok = _job_id.set(job_id), _org.set(org), _actor.set(actor)
    try:
        yield
    finally:
        _job_id.reset(job_tok)
        _org.reset(org_tok)
        _actor.reset(actor_tok)


@contextmanager
def run_context(run_id: str, *, secrets: tuple[str, ...] = ()) -> Iterator[None]:
    """Bind a run's id and, while it runs, a redactor seeded with its resolved ``${secrets.X}``."""
    id_token = _run_id.set(run_id)
    red_token = _run_redactor.set(Redactor(None, values=list(secrets)) if secrets else None)
    try:
        yield
    finally:
        _run_id.reset(id_token)
        _run_redactor.reset(red_token)
