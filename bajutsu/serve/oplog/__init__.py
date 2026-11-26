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

from ._context_filter import _ContextFilter as _ContextFilter
from ._functions import _FORMATS as _FORMATS
from ._functions import _SCHEMA_KEYS as _SCHEMA_KEYS
from ._functions import _SENSITIVE_KEYS as _SENSITIVE_KEYS
from ._functions import (
    EVENTS,
    bind_actor,
    bind_request,
    configure,
    job_context,
    log_event,
    make_handler,
    new_request_id,
    request_context,
    reset,
    run_context,
)
from ._functions import _extras as _extras
from ._functions import _is_sensitive_key as _is_sensitive_key
from ._functions import _mask_values as _mask_values
from ._functions import _run_redactor as _run_redactor
from ._json_formatter import _JsonFormatter as _JsonFormatter
from ._name_mask_filter import _NameMaskFilter as _NameMaskFilter
from ._shared import _CONTEXT_KEYS as _CONTEXT_KEYS
from ._shared import _RESERVED as _RESERVED
from ._shared import _actor as _actor
from ._shared import _job_id as _job_id
from ._shared import _org as _org
from ._shared import _request_id as _request_id
from ._shared import _run_id as _run_id
from ._text_formatter import _TextFormatter as _TextFormatter

__all__ = [
    "EVENTS",
    "bind_actor",
    "bind_request",
    "configure",
    "job_context",
    "log_event",
    "make_handler",
    "new_request_id",
    "request_context",
    "reset",
    "run_context",
]
