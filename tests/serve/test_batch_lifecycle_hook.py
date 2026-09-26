"""Tests for BatchContext and BatchLifecycleHook protocol (BE-0435)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.serve import batch_provider as bp


def _make_request() -> bp.BatchRequest:
    return bp.BatchRequest(
        provider="devicefarm",
        scenario="smoke.yaml",
        target="demo",
        config="bajutsu.config.yaml",
        platform="android",
        app_path="app.apk",
    )


def test_batch_context_fields_and_defaults(tmp_path: Path) -> None:
    request = _make_request()
    ctx = bp.BatchContext(request=request, work_dir=tmp_path, job_id="job-1")
    assert ctx.request is request
    assert ctx.work_dir == tmp_path
    assert ctx.job_id == "job-1"
    assert ctx.launch_env == {}


def test_batch_context_launch_env_is_independent() -> None:
    # Each BatchContext gets its own launch_env dict (no shared mutable default).
    request = _make_request()
    ctx1 = bp.BatchContext(request=request, work_dir=Path("/a"), job_id="j1")
    ctx2 = bp.BatchContext(request=request, work_dir=Path("/b"), job_id="j2")
    ctx1.launch_env["K"] = "V"
    assert "K" not in ctx2.launch_env


def test_batch_context_is_a_dataclass() -> None:
    import dataclasses

    assert dataclasses.is_dataclass(bp.BatchContext)
    fields = {f.name for f in dataclasses.fields(bp.BatchContext)}
    assert fields >= {"request", "work_dir", "job_id", "launch_env"}


def test_hook_no_op_after_run_via_explicit_subclassing() -> None:
    # Explicitly subclassing BatchLifecycleHook gives a no-op after_run.
    class OnlyBefore(bp.BatchLifecycleHook):
        def before_submit(self, ctx: bp.BatchContext) -> None:
            ctx.launch_env["X"] = "1"

    hook = OnlyBefore()
    ctx = bp.BatchContext(request=_make_request(), work_dir=Path("/w"), job_id="j")
    hook.before_submit(ctx)
    hook.after_run(ctx, None)  # must not raise


def test_hook_no_op_before_submit_via_explicit_subclassing() -> None:
    # Explicitly subclassing BatchLifecycleHook gives a no-op before_submit.
    class OnlyAfter(bp.BatchLifecycleHook):
        def after_run(self, ctx: bp.BatchContext, verdict: object) -> None:
            pass

    hook = OnlyAfter()
    ctx = bp.BatchContext(request=_make_request(), work_dir=Path("/w"), job_id="j")
    hook.before_submit(ctx)  # must not raise
    assert ctx.launch_env == {}


def test_batch_lifecycle_hook_is_a_protocol() -> None:
    from typing import runtime_checkable

    import typing

    # Protocol membership: class must be from typing
    assert issubclass(bp.BatchLifecycleHook, typing.Protocol)  # type: ignore[arg-type]
