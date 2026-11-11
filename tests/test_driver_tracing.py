"""Tests for BE-0415: per-scenario Python<->driver call tracing (`bajutsu run --trace-driver`).

`TracingDriver` is exercised directly against `FakeDriver`; the transport/subprocess wraps are
exercised directly against `XcuitestDriver`/`AdbDriver` built with a stub transport / fake `run`,
since the `fake` backend itself produces no `transport` or `subprocess` records (see this item's own
Progress unit 8). `tests/runner/test_pipeline.py`'s `test_trace_driver_*` tests cover the real,
unstubbed pipeline path (`run_all(..., trace_driver=True)`) end to end; `tests/test_cli.py`'s
`test_run_trace_driver_flag_reaches_run_and_report` covers `--trace-driver` threading from the CLI
down to `run_and_report`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bajutsu.common.drivers import base, tracing
from bajutsu.common.drivers.actuation import ActuationReporter
from bajutsu.common.drivers.adb import (
    ActOutcome,
    ActRequest,
    AdbActUnsupported,
    AdbDriver,
    HierarchyRead,
)
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.drivers.tracing import TracingDriver
from bajutsu.common.drivers.xcuitest import XcuitestDriver, _Reply

_DUMP = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node index="0" text="OK" resource-id="ok" class="android.widget.Button"
      content-desc="" enabled="true" checked="false" selected="false" bounds="[0,100][200,200]" />
  </node>
</hierarchy>
UI hierarchy dumped to: /dev/tty"""


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
        "nativeZ": None,
    }


# --- TracingDriver (unit 1) ---


def test_tracing_driver_times_a_traced_method() -> None:
    traced = TracingDriver(FakeDriver([_el("ok", "OK", ["button"])]))
    with tracing.open_trace() as ctx:
        traced.tap({"id": "ok"})
    assert [r.category for r in ctx.records] == ["driver"]
    assert ctx.records[0].name == "tap"
    assert ctx.records[0].elapsed_s >= 0.0


def test_tracing_driver_passes_through_a_data_attribute_unwrapped() -> None:
    fake = FakeDriver([])
    traced = TracingDriver(fake)
    assert traced.name == fake.name  # not in the traced-method set, and not callable either


def test_tracing_driver_records_nothing_when_no_trace_is_open() -> None:
    traced = TracingDriver(FakeDriver([_el("ok", "OK", ["button"])]))
    traced.tap({"id": "ok"})  # no `open_trace()` — must run untraced, not raise


def test_tracing_driver_preserves_a_capability_protocol_negative() -> None:
    # `FakeDriver` has no `settled_query`: a proxy that declared it as a real method would answer
    # `isinstance` True regardless of the wrapped driver, which is exactly what `__getattr__`-only
    # avoids.
    fake = FakeDriver([])
    assert not isinstance(fake, base.SettledReadProvider)
    assert not isinstance(TracingDriver(fake), base.SettledReadProvider)


def test_tracing_driver_preserves_a_capability_protocol_positive() -> None:
    # `FakeDriver` does implement `InterruptionPolicyTarget` — the proxy must read that through too.
    fake = FakeDriver([])
    assert isinstance(fake, base.InterruptionPolicyTarget)
    assert isinstance(TracingDriver(fake), base.InterruptionPolicyTarget)


def test_tracing_driver_preserves_actuation_reporting() -> None:
    # `ActuationReporter` lives outside `base.*` (`bajutsu/common/drivers/actuation/`), so it is easy
    # to miss when listing every protocol a driver might satisfy — missing it here silently drops
    # every step's actuation evidence for the whole run once `--trace-driver` is on, since
    # `_StepRunner` reads `drain_actuations()` through an `isinstance` check
    # (`bajutsu/common/orchestrator/types/_functions.py`).
    fake = FakeDriver([_el("ok", "OK", ["button"])])
    traced = TracingDriver(fake)
    assert isinstance(traced, ActuationReporter)
    traced.tap({"id": "ok"})
    assert traced.drain_actuations().records  # the tap actually reached FakeDriver, not a stub


def test_tracing_covers_every_runtime_checkable_protocol_under_drivers() -> None:
    # Guards against a repeat of the bug this exact check would have caught: `tracing._PROTOCOLS`
    # once missed `ActuationReporter` (it lives outside `base.*`), silently dropping every step's
    # actuation evidence under `--trace-driver`. Walks the real package tree rather than trusting a
    # hand-maintained list of modules to check, so a protocol added anywhere under `drivers/` in the
    # future — in a new module, not just a new member on an existing one — fails this until
    # `tracing._PROTOCOLS` names it too.
    import importlib
    import pkgutil

    drivers_pkg = importlib.import_module(tracing.__package__)

    found: set[type] = set()
    for _finder, name, _ispkg in pkgutil.walk_packages(
        drivers_pkg.__path__, prefix="bajutsu.common.drivers."
    ):
        module = importlib.import_module(name)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and getattr(attr, "_is_protocol", False)
                and getattr(attr, "_is_runtime_protocol", False)
            ):
                found.add(attr)

    assert found, "the walk itself found nothing — check pkgutil.walk_packages's prefix"
    assert found == set(tracing._PROTOCOLS)


# --- XcuitestDriver transport wrap (unit 3) ---


def test_xcuitest_transport_wrap_records_a_get_round_trip() -> None:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        return _Reply(status="ok", elements=[])

    with tracing.open_trace() as ctx:
        driver = XcuitestDriver(transport=transport, sleep=lambda _s: None)
        driver.query()
    transport_records = [r for r in ctx.records if r.category == "transport"]
    assert transport_records
    assert transport_records[0].name == "GET /elements"
    assert transport_records[0].response is None  # a read carries no response outcome


def test_xcuitest_transport_wrap_records_a_post_round_trip_with_its_status() -> None:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        return _Reply(status="ok")

    with tracing.open_trace() as ctx:
        driver = XcuitestDriver(transport=transport, sleep=lambda _s: None)
        driver.tap_point((1.0, 1.0))
    posts = [r for r in ctx.records if r.category == "transport" and r.name == "POST /tap"]
    assert posts
    assert posts[0].response == {"status": "ok"}


def test_xcuitest_driver_built_without_an_open_trace_installs_no_wrap() -> None:
    calls: list[tuple[str, str]] = []

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        calls.append((method, path))
        return _Reply(status="ok", elements=[])

    driver: XcuitestDriver = XcuitestDriver(transport=transport, sleep=lambda _s: None)
    driver.query()
    assert calls  # runs normally; no trace context, so nothing to assert about records


def test_traced_xcuitest_driver_records_nothing_once_its_trace_closes() -> None:
    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        return _Reply(status="ok", elements=[])

    with tracing.open_trace() as ctx:
        driver = XcuitestDriver(transport=transport, sleep=lambda _s: None)
    driver.query()  # outside the `with`: the wrap was installed, but no trace is open now
    assert not ctx.records


# --- AdbDriver run/fetch/act wraps (unit 4) ---


def test_adb_run_wrap_records_a_subprocess_call() -> None:
    with tracing.open_trace() as ctx:
        driver = AdbDriver("U", run=lambda _args: _DUMP)
        driver.query()
    subprocess_records = [r for r in ctx.records if r.category == "subprocess"]
    assert subprocess_records
    assert "dump" in subprocess_records[0].name


def test_adb_driver_built_without_an_open_trace_installs_no_wrap() -> None:
    driver = AdbDriver("U", run=lambda _args: _DUMP)
    driver.query()  # must run normally with no trace open


def test_adb_fetch_hierarchy_fetch_clock_and_act_wraps_record_transport() -> None:
    # A single resident-channel `tap` exercises all three wraps in one gesture: `_capture_mark`
    # reads `fetch_clock` before injecting, `_device_act` reads `act`, and the resolve before it
    # reads `fetch_hierarchy` — matching the resident-channel path a real device exercises.
    def fetch_hierarchy(_since: float | None) -> HierarchyRead:
        return HierarchyRead(_DUMP, mark=1000.0)

    def act(_request: ActRequest) -> ActOutcome:
        return ActOutcome(acted=True, published_mark=1001.0)

    with tracing.open_trace() as ctx:
        driver = AdbDriver(
            "U",
            run=lambda _args: _DUMP,
            fetch_hierarchy=fetch_hierarchy,
            fetch_clock=lambda: 999.0,
            act=act,
        )
        driver.tap({"id": "ok"})
    names = {r.name for r in ctx.records if r.category == "transport"}
    assert names == {"fetch_hierarchy", "fetch_clock", "act"}
    act_record = next(r for r in ctx.records if r.name == "act")
    assert act_record.response == {"acted": True, "published_mark": 1001.0}


def test_adb_act_wrap_records_elapsed_time_even_when_act_raises() -> None:
    def act(_request: ActRequest) -> ActOutcome:
        raise AdbActUnsupported("no /act endpoint")

    with tracing.open_trace() as ctx:
        driver = AdbDriver("U", run=lambda _args: _DUMP, act=act)
        driver.tap({"id": "ok"})  # degrades to the coordinate path; must not raise past `tap`
    act_records = [r for r in ctx.records if r.name == "act"]
    assert act_records
    assert act_records[0].response is None  # no outcome to report — `act` never returned one


def test_adb_wraps_pass_through_once_their_trace_closes() -> None:
    # A wrap is installed once, at construction, if a trace was open then — but the trace it reads
    # per call is read fresh each time (see `tracing.py`'s module docstring): a wrap called after
    # its trace has closed must still call straight through, recording nothing.
    from bajutsu.common.drivers.adb.adb_driver import (
        _traced_act,
        _traced_fetch_clock,
        _traced_fetch_hierarchy,
        _traced_run,
    )

    with tracing.open_trace():
        run_wrap = _traced_run(lambda _args: _DUMP)
        fetch_wrap = _traced_fetch_hierarchy(lambda _since: HierarchyRead(_DUMP))
        clock_wrap = _traced_fetch_clock(lambda: 1.0)
        act_wrap = _traced_act(lambda _req: ActOutcome(acted=True, published_mark=None))

    request = ActRequest(
        kind="tap", identity=("id", "ok", "", ""), index=0, count=1, since=None, duration_ms=None
    )
    assert run_wrap([]) == _DUMP
    assert fetch_wrap(None).text == _DUMP
    assert clock_wrap() == 1.0
    assert act_wrap(request).acted is True


def test_adb_server_text_entry_records_its_transport_call_without_the_text() -> None:
    # A typed value may be a secret, so the trace keeps the call's name and timing alone.
    typed: list[str] = []
    with tracing.open_trace() as ctx:
        driver = AdbDriver(
            "U", run=lambda _args: _DUMP, type_on_device=lambda t, _since: typed.append(t)
        )
        driver.type_text("秘密")
    assert typed == ["秘密"]
    record = next(r for r in ctx.records if r.category == "transport" and r.name == "type")
    assert "秘密" not in repr(record)


def test_traced_type_passes_through_once_the_trace_has_closed() -> None:
    # The wrapper is chosen at construction and outlives the trace that chose it.
    from bajutsu.common.drivers.adb.adb_driver import _traced_type

    typed: list[str] = []
    _traced_type(lambda t, _since: typed.append(t))("馬", None)
    assert typed == ["馬"]


def test_adb_server_gesture_records_its_transport_call() -> None:
    sent: list[object] = []
    with tracing.open_trace() as ctx:
        driver = AdbDriver(
            "U",
            run=lambda _args: _DUMP,
            gesture_on_device=lambda start, end: sent.append((start, end)),
        )
        driver._gesture_fn((((1.0, 2.0), (3.0, 4.0))), (((5.0, 6.0), (7.0, 8.0))))  # type: ignore[misc]
    assert len(sent) == 1
    assert any(r.category == "transport" and r.name == "gesture" for r in ctx.records)


def test_traced_gesture_passes_through_once_the_trace_has_closed() -> None:
    from bajutsu.common.drivers.adb.adb_driver import _traced_gesture

    sent: list[object] = []
    _traced_gesture(lambda start, end: sent.append((start, end)))(
        ((1.0, 2.0), (3.0, 4.0)), ((5.0, 6.0), (7.0, 8.0))
    )
    assert len(sent) == 1


def test_adb_run_text_runs_normally_with_no_trace_open(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "bajutsu.common.drivers.adb.adb_driver.subprocess.run", lambda *a, **k: None
    )
    driver = AdbDriver("U", run=lambda _args: _DUMP)
    driver.type_text("hi")  # no `open_trace()` — must reach the stubbed subprocess call, not raise


def test_adb_run_text_reads_the_ambient_trace_directly(monkeypatch: Any) -> None:
    # `_run_text` is a plain `@staticmethod` (BE-0155's secret-safe stdin path), so it cannot be
    # wrapped at construction time like `run`/`fetch_hierarchy`/`fetch_clock`/`act` — it consults
    # `tracing.current_trace()` itself instead.
    monkeypatch.setattr(
        "bajutsu.common.drivers.adb.adb_driver.subprocess.run", lambda *a, **k: None
    )
    driver = AdbDriver("U", run=lambda _args: _DUMP)
    with tracing.open_trace() as ctx:
        driver.type_text("hi")
    subprocess_records = [r for r in ctx.records if r.category == "subprocess"]
    assert subprocess_records


def test_adb_background_screenshot_records_its_own_subprocess_entry(monkeypatch: Any) -> None:
    # `screenshot_in_background` defers the real capture to a worker thread, so `TracingDriver`'s
    # generic per-call wrap (which can only time the near-instant call that starts the thread and
    # returns `join`) cannot see it — this records from inside the thread instead, and must still
    # see the caller's open trace despite `threading.Thread` starting with a fresh, empty context.
    from bajutsu.common.backend_cli.adb.env import Env

    monkeypatch.setattr(Env, "_run_capture", staticmethod(lambda cmd, path: None))
    driver = AdbDriver("U", run=lambda _args: _DUMP)
    with tracing.open_trace() as ctx:
        join = driver.screenshot_in_background("/tmp/be0415-test-shot.png")
        join()
    records = [r for r in ctx.records if r.name == "screenshot_in_background"]
    assert records
    assert records[0].category == "subprocess"
