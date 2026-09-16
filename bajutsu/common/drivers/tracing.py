"""Per-scenario Python<->driver call tracing (BE-0415), opt-in via `bajutsu run --trace-driver`.

Composes at existing seams instead of monkey-patching (`trace_run.py`'s approach, BE-0407): a
`TracingDriver` proxy times `Driver` protocol calls, and `XcuitestDriver`/`AdbDriver` time their own
transport/subprocess round trips internally, each checking at its own construction time whether a
trace is already open. All of them read the *ambient* trace via a `ContextVar` rather than a
constructor parameter, because the driver is built several calls below any code that knows whether
`--trace-driver` was passed (`RunEnvironment.start` -> `backends.make_driver`) — threading an
explicit parameter would touch every `RunEnvironment` subclass's constructor for a diagnostic
feature. Lives in `drivers/` (not `runner/`) so `XcuitestDriver`/`AdbDriver` and
`runner/pipeline.py` can all import it without a cycle: `runner/` already imports from `drivers/`,
never the reverse.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, get_protocol_members

from bajutsu.common.drivers import base
from bajutsu.common.drivers.actuation import ActuationReporter

# The `Driver` protocol's own methods (`base.Driver`), plus the two capability-protocol methods
# BE-0407's trace_run.py also measured — `drain_interruptions` / `settled_query` — present only on
# a driver that opts into the matching capability protocol. Fixed set, not introspected from the
# protocol at runtime, so a traced call is decided by a plain membership check.
_TRACED_METHODS = frozenset(
    {
        "query",
        "tap",
        "is_tappable",
        "tap_point",
        "double_tap",
        "long_press",
        "swipe",
        "scroll",
        "back",
        "pinch",
        "rotate",
        "type_text",
        "delete_text",
        "select_all",
        "copy_selection",
        "select_option",
        "set_picker_value",
        "handle_system_alert",
        "system_alert_labels",
        "dismiss_blocking_tip",
        "wait_for",
        "screenshot",
        "capabilities",
        "drain_interruptions",
        "settled_query",
    }
)

# Every capability/trait protocol *any* driver in this codebase can be `isinstance`-checked against
# — every `@runtime_checkable` protocol under `bajutsu/common/drivers/`, `base.*` plus
# `ActuationReporter` (a step's own drained actuations, `bajutsu/common/drivers/actuation/`).
# `TracingDriver.__init__` installs each one's members (`typing.get_protocol_members`, not a
# hand-maintained list — a protocol added or changed here needs no matching edit there) as real
# instance attributes, only when the wrapped driver actually `isinstance`s it. See the class's own
# docstring for why real attributes, not `__getattr__`, are what makes `isinstance` read correctly.
# `tests/test_driver_tracing.py` walks the package to confirm this tuple never misses one.
_PROTOCOLS: tuple[type, ...] = (
    base.Driver,
    base.AppCrashPollResettable,
    base.AppCrashSignal,
    base.Queryable,
    base.BackendLifecycle,
    base.BackgroundScreenshotProvider,
    base.EvidenceProvider,
    base.InterruptionPolicyTarget,
    base.RawSourceProvider,
    base.ReadLagProvider,
    base.ReadOrderProvider,
    base.SettledCacheInvalidator,
    base.SettledReadProvider,
    base.ViewportProvider,
    ActuationReporter,
)


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """One timed Python<->driver call, attributed to the step and crash-recovery attempt it
    happened during."""

    category: str  # "driver" | "transport" | "subprocess"
    step: str | None
    name: str
    started_at: float
    elapsed_s: float
    response: dict[str, Any] | None = None
    attempt: int = 1


@dataclass(slots=True)
class TraceContext:
    """One scenario's accumulated trace: driver/transport/subprocess records plus each step's own
    wall time. `current_step` is set by `traced_step` for the duration of one step. `attempt` is
    set by `_ScenarioRunner._run_one_impl`'s retry loop, once per crash-recovery attempt — a fresh
    lease (and driver) starts each attempt, but this one `TraceContext` spans every attempt, so
    without it a retry's records would be indistinguishable from the attempt it recovered from
    (same step keys, since each attempt's `StepLoopState` restarts its counter at 0)."""

    records: list[TraceRecord] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    current_step: str | None = None
    attempt: int = 1

    def record(
        self,
        category: str,
        name: str,
        started_at: float,
        elapsed_s: float,
        response: dict[str, Any] | None = None,
    ) -> None:
        self.records.append(
            TraceRecord(
                category, self.current_step, name, started_at, elapsed_s, response, self.attempt
            )
        )


# Ambient, not threaded as a parameter (see module docstring). Each `run_one` call sets its own
# context and resets it in a `finally`, so a worker thread reused by `ThreadPoolExecutor.map` across
# scenarios never sees a stale value — the set/reset pair, not thread identity, scopes it.
_TRACE: ContextVar[TraceContext | None] = ContextVar("bajutsu_driver_trace", default=None)


@contextmanager
def open_trace() -> Iterator[TraceContext]:
    """Open a fresh trace context for the duration of the block; always reset on exit."""
    ctx = TraceContext()
    token = _TRACE.set(ctx)
    try:
        yield ctx
    finally:
        _TRACE.reset(token)


def current_trace() -> TraceContext | None:
    """The trace context open on this call stack right now, or None when `--trace-driver` is off."""
    return _TRACE.get()


@contextmanager
def traced_step(key: str) -> Iterator[None]:
    """Attribute every record made inside the block to step `key`, and log the step's own wall time.

    A no-op when no trace is open, so `_StepRunner._run_one` can call this unconditionally.
    """
    ctx = _TRACE.get()
    if ctx is None:
        yield
        return
    prev = ctx.current_step
    ctx.current_step = key
    start = time.perf_counter()
    try:
        yield
    finally:
        ctx.steps.append(
            {"step": key, "wall_s": time.perf_counter() - start, "attempt": ctx.attempt}
        )
        ctx.current_step = prev


def trace_document(scenario_name: str, ctx: TraceContext) -> dict[str, Any]:
    """The JSON-serializable shape written to `driver_trace.json`."""
    return {
        "scenario": scenario_name,
        "steps": ctx.steps,
        "records": [
            {
                "category": r.category,
                "step": r.step,
                "name": r.name,
                "started_at": r.started_at,
                "elapsed_s": r.elapsed_s,
                "response": r.response,
                "attempt": r.attempt,
            }
            for r in ctx.records
        ],
    }


class TracingDriver:
    """Delegating proxy over a concrete driver, timing only the traced `Driver`/capability methods.

    Installs every member of every capability protocol `wrapped` actually satisfies as a real
    instance attribute, rather than answering everything through one generic `__getattr__`:
    `isinstance()` against a `@runtime_checkable` protocol resolves via `inspect.getattr_static`,
    which reads `type(obj).__mro__` and `obj.__dict__` directly and never calls `__getattr__` or
    `__getattribute__` — so a proxy that forwards purely through `__getattr__` can `hasattr()`
    correctly but can *never* pass a positive `isinstance` check for any protocol, no matter what it
    delegates to (confirmed empirically against CPython 3.13's `typing` implementation; a proxy that
    instead declared `drain_interruptions`/`settled_query` etc. as real class-level methods would
    overcorrect the other way, answering `isinstance` `True` for every driver it wraps, including
    ones that don't actually support them). Installing the real members once, at construction,
    keeps the wrapped driver's true capability set legible to every `isinstance` probe in the
    orchestrator, in both directions. `__getattr__` is kept as a plain fallback for anything reached
    outside these protocols' own member sets.
    """

    def __init__(self, wrapped: base.Driver) -> None:
        self._wrapped = wrapped
        for protocol in _PROTOCOLS:
            if not isinstance(wrapped, protocol):
                continue
            for name in get_protocol_members(protocol):
                setattr(self, name, self._forward(name))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def _forward(self, name: str) -> Any:
        attr = getattr(self._wrapped, name)
        if name not in _TRACED_METHODS or not callable(attr):
            return attr

        def _timed(*args: Any, **kwargs: Any) -> Any:
            ctx = _TRACE.get()
            if ctx is None:
                return attr(*args, **kwargs)
            started_at = time.time()
            t0 = time.perf_counter()
            try:
                return attr(*args, **kwargs)
            finally:
                ctx.record("driver", name, started_at, time.perf_counter() - t0)

        return _timed
