"""Operational-logging contract (BE-0055).

Operational logs are non-deterministic (timestamps, ordering), so these tests assert the
*schema and invariants* of the channel — not byte-equality like evidence. Each test maps to one
of the contract's machine-checkable invariants: secret-free, correlation-id propagation,
structured schema, gate-clean, and the event taxonomy.
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
import sys

import pytest

from bajutsu.serve import oplog


def _emit(
    stream: io.StringIO, *, secrets: tuple[str, ...] = (), fmt: str = "json"
) -> logging.Logger:
    """A logger writing through a freshly built oplog handler into *stream* (isolated from root)."""
    logger = logging.getLogger(f"test.oplog.{id(stream)}")
    logger.handlers = [oplog.make_handler(stream=stream, fmt=fmt, secrets=secrets)]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(ln) for ln in stream.getvalue().splitlines() if ln.strip()]


# 1. Structured schema (serve).
def test_json_line_has_required_keys_and_types() -> None:
    stream = io.StringIO()
    logger = _emit(stream)
    with oplog.request_context("req-123"):
        logger.info("hello")
    (record,) = _lines(stream)
    assert isinstance(record["ts"], str)
    assert record["level"] == "INFO"
    assert record["logger"] == logger.name
    assert record["msg"] == "hello"
    assert record["request_id"] == "req-123"


def test_absent_correlation_ids_are_omitted_not_null() -> None:
    stream = io.StringIO()
    logger = _emit(stream)
    logger.info("no context bound")
    (record,) = _lines(stream)
    for key in ("request_id", "org", "actor", "job_id", "run_id"):
        assert key not in record


# 2. Secret-free (redaction) — value-based.
def test_known_secret_value_is_masked() -> None:
    stream = io.StringIO()
    logger = _emit(stream, secrets=("s3cr3t-token",))
    logger.info("connecting with s3cr3t-token now")
    (record,) = _lines(stream)
    assert "s3cr3t-token" not in json.dumps(record)
    assert "[REDACTED]" in str(record["msg"])


# 2. Secret-free (redaction) — key-based.
def test_sensitive_field_name_is_masked_regardless_of_value() -> None:
    stream = io.StringIO()
    logger = _emit(stream)
    logger.info("auth attempt", extra={"authorization": "Bearer abc.def"})
    (record,) = _lines(stream)
    assert record["authorization"] == "[REDACTED]"
    assert "abc.def" not in json.dumps(record)


def test_known_secret_value_in_a_non_string_field_is_masked() -> None:
    # Value-masking runs on the whole rendered line, so a secret reaches the sink scrubbed even
    # when it rides inside a non-string field that name-based masking would not catch.
    stream = io.StringIO()
    logger = _emit(stream, secrets=("s3cr3t-token",))
    logger.info("payload", extra={"meta": {"nested": "s3cr3t-token"}})
    (record,) = _lines(stream)
    assert "s3cr3t-token" not in json.dumps(record)


def test_invalid_log_level_is_rejected_with_a_clear_error() -> None:
    with pytest.raises(ValueError, match="BAJUTSU_LOG_LEVEL"):
        oplog.configure(level="verbose", stream=io.StringIO())


def test_invalid_log_format_is_rejected_with_a_clear_error() -> None:
    with pytest.raises(ValueError, match="BAJUTSU_LOG_FORMAT"):
        oplog.configure(fmt="yaml", stream=io.StringIO())


def test_log_format_is_case_insensitive() -> None:
    stream = io.StringIO()
    logger = _emit(stream, fmt="JSON")
    logger.info("hi")
    (record,) = _lines(stream)  # parses as JSON, so "JSON" selected the json formatter
    assert record["msg"] == "hi"


def test_log_event_rejects_an_unregistered_event() -> None:
    stream = io.StringIO()
    logger = _emit(stream)
    with pytest.raises(ValueError, match="unknown operational event"):
        oplog.log_event(logger, "run.not_a_real_event", "unregistered name")


def test_configure_takes_over_a_preexisting_root_handler() -> None:
    # A handler installed before serve startup must not write an unredacted line past our sink.
    leaked = io.StringIO()
    stray = logging.StreamHandler(leaked)
    root = logging.getLogger()
    root.addHandler(stray)
    ours = io.StringIO()
    try:
        oplog.configure(fmt="json", level="INFO", secrets=("hunter2",), stream=ours)
        logging.getLogger("x").warning("leaked hunter2 here")
    finally:
        oplog.reset()
        root.removeHandler(stray)
    assert leaked.getvalue() == ""  # the stray handler was removed, so it wrote nothing
    assert "hunter2" not in ours.getvalue()


def test_root_filter_masks_third_party_loggers() -> None:
    # The redactor sits at the root handler, so a library that never heard of oplog is scrubbed too.
    buf = io.StringIO()
    oplog.configure(fmt="json", level="INFO", secrets=("hunter2",), stream=buf)
    try:
        logging.getLogger("some.third.party").warning("leaked hunter2 here")
    finally:
        oplog.reset()
    assert "hunter2" not in buf.getvalue()
    assert "[REDACTED]" in buf.getvalue()


# 3. Correlation-id propagation.
def test_request_records_share_one_request_id() -> None:
    stream = io.StringIO()
    logger = _emit(stream)
    with oplog.request_context("req-xyz"):
        logger.info("first")
        logger.info("second")
    first, second = _lines(stream)
    assert first["request_id"] == second["request_id"] == "req-xyz"


def test_worker_records_carry_job_run_and_org() -> None:
    stream = io.StringIO()
    logger = _emit(stream)
    with oplog.job_context(job_id="job-1", org="acme", actor="alice"), oplog.run_context("run-9"):
        logger.info("running")
    (record,) = _lines(stream)
    assert record["job_id"] == "job-1"
    assert record["org"] == "acme"
    assert record["actor"] == "alice"
    assert record["run_id"] == "run-9"


# Redaction — per-run scoped secret values (resolved ${secrets.X}).
def test_run_scoped_secret_is_masked_only_within_the_run() -> None:
    stream = io.StringIO()
    logger = _emit(stream)  # no static secrets
    with oplog.run_context("run-1", secrets=("run-only-secret",)):
        logger.info("used run-only-secret inside")
    logger.info("run-only-secret outside the run")
    inside, outside = _lines(stream)
    assert "run-only-secret" not in str(inside["msg"])
    assert "run-only-secret" in str(outside["msg"])  # scope fell away with the run


# 5. Event taxonomy.
def test_event_registry_lists_the_stable_names() -> None:
    expected = {
        "run.dispatched",
        "run.recorded",
        "oauth.login",
        "quota.rejected",
        "worker.job.started",
        "worker.job.finished",
        "artifact.upload.failed",
    }
    assert expected <= oplog.EVENTS


def test_explicit_correlation_field_is_kept_and_context_fills_the_rest() -> None:
    # The control plane names ids directly (it binds nothing in context); the contextvar only fills
    # the ids left unset. This is the path operations.py uses for run.dispatched / quota.rejected.
    stream = io.StringIO()
    logger = _emit(stream)
    with oplog.request_context("req-1"):
        oplog.log_event(logger, "run.dispatched", "dispatched", org="acme", job_id="j1")
    (record,) = _lines(stream)
    assert record["org"] == "acme"
    assert record["job_id"] == "j1"
    assert record["request_id"] == "req-1"


def test_log_event_sets_the_event_field() -> None:
    stream = io.StringIO()
    logger = _emit(stream)
    oplog.log_event(logger, "run.dispatched", "dispatched a run")
    (record,) = _lines(stream)
    assert record["event"] == "run.dispatched"
    assert record["msg"] == "dispatched a run"


def test_text_format_is_human_readable_not_json() -> None:
    stream = io.StringIO()
    logger = _emit(stream, fmt="text")
    logger.info("plain line")
    out = stream.getvalue()
    assert "plain line" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


# 5. Event taxonomy — the control-plane dispatch seam emits run.dispatched / quota.rejected.
def test_dispatch_seam_emits_run_dispatched_then_quota_rejected() -> None:
    from pathlib import Path

    from bajutsu import serve as srv
    from bajutsu.serve import operations as ops

    class _NoopExecutor:
        def dispatch(self, state: srv.ServeState, job: srv.Job) -> None:
            pass

    buf = io.StringIO()
    oplog.configure(fmt="json", level="INFO", stream=buf)
    try:
        state = srv.ServeState(runs_dir=Path("runs"), executor=_NoopExecutor(), max_concurrent=1)
        dispatched, err = ops._register_and_dispatch(state, srv.Job(id="", cmd=[], org="acme"))
        assert err is None and dispatched is not None
        state.jobs[dispatched.id].status = "running"  # now at the cap
        rejected, capped = ops._register_and_dispatch(state, srv.Job(id="", cmd=[], org="acme"))
        assert rejected is None and capped is not None
    finally:
        oplog.reset()
    events = {
        json.loads(ln)["event"]: json.loads(ln)
        for ln in buf.getvalue().splitlines()
        if ln.strip() and "event" in json.loads(ln)
    }
    assert events["run.dispatched"]["job_id"] == dispatched.id
    assert events["run.dispatched"]["org"] == "acme"
    assert "quota.rejected" in events


# 4. Gate-clean (two-tier): oplog stays stdlib-only — importing it pulls in no heavy server dep.
def test_oplog_import_pulls_no_heavy_deps() -> None:
    forbidden = ("fastapi", "uvicorn", "redis", "rq", "sqlalchemy", "boto3", "authlib")
    code = (
        "import sys\n"
        "import bajutsu.serve.oplog\n"
        f"leaked = sorted(m for m in sys.modules if m.split('.')[0] in {forbidden!r})\n"
        "sys.stdout.write(','.join(leaked))\n"
        "sys.exit(1 if leaked else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, f"oplog leaked heavy deps: {result.stdout.strip()}"
