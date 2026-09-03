"""Run `bajutsu run …` with every driver call, transport request, subprocess, and evidence
capture timed and attributed to the step in flight.

No product code is modified: the wrappers below are installed on the imported modules before the
CLI starts, so the numbers describe the shipped code paths. Threads are supported (the run
pipeline's `ThreadPoolExecutor` workers); a run that forks worker processes is not traced there.

Usage (from the repo root, with the same arguments `bajutsu run` takes):

    uv run python roadmaps/BE-XXXX-step-latency-driver-internal-tuning/misc/step-performance/trace_run.py --out trace.json -- \\
        run --target showcase-swiftui --udid <UDID> --backend ios --config demos/showcase/showcase.config.yaml

The summary is printed on exit; `--out` also writes every timed call as JSON for a finer cut.
"""

from __future__ import annotations

import atexit
import functools
import json
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_RECORDS: list[dict[str, Any]] = []
_STEPS: list[dict[str, Any]] = []
_LOCAL = threading.local()

DRIVER_METHODS = (
    "query",
    "tap",
    "tap_point",
    "double_tap",
    "long_press",
    "swipe",
    "scroll",
    "type_text",
    "delete_text",
    "is_tappable",
    "wait_for",
    "screenshot",
    "system_alert_labels",
    "handle_system_alert",
    "drain_interruptions",
    "dismiss_blocking_tip",
    "settled_query",
    "set_picker_value",
    "select_option",
    "back",
)


def _step_key() -> str | None:
    stack: list[str] = getattr(_LOCAL, "stack", [])
    return stack[-1] if stack else None


def _record(category: str, name: str, elapsed: float) -> None:
    with _LOCK:
        _RECORDS.append(
            {"step": _step_key(), "category": category, "name": name, "s": round(elapsed, 6)}
        )


def _timed(category: str, name_of: Callable[..., str]) -> Callable[[Any], Any]:
    def deco(fn: Any) -> Any:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                _record(category, name_of(*args, **kwargs), time.perf_counter() - t0)

        return wrapper

    return deco


def _argv_summary(argv: Any) -> str:
    if not isinstance(argv, (list, tuple)):
        return str(argv)[:60]
    words = [str(w) for w in argv]
    # Drop the device selector so every adb/simctl call folds into one bucket per verb.
    out: list[str] = []
    skip = False
    for w in words:
        if skip:
            skip = False
            continue
        if w in {"-s", "--udid"}:
            skip = True
            continue
        out.append(Path(w).name if "/" in w else w)
        if len(out) >= 4:
            break
    return " ".join(out)


def _install_subprocess_wrappers() -> None:
    subprocess.run = _timed("subprocess", lambda args, *a, **k: _argv_summary(args))(  # type: ignore[assignment]
        subprocess.run
    )
    subprocess.check_output = _timed("subprocess", lambda args, *a, **k: _argv_summary(args))(  # type: ignore[assignment]
        subprocess.check_output
    )


def _install_driver_wrappers() -> None:
    from bajutsu.common.drivers import adb, fake, xcuitest

    # The fake is wrapped too, so the tracer itself can be exercised on a device-free machine.
    for cls in (xcuitest.XcuitestDriver, adb.AdbDriver, fake.FakeDriver):
        for name in DRIVER_METHODS:
            fn = getattr(cls, name, None)
            if fn is None:
                continue
            setattr(cls, name, _timed("driver", lambda *a, _n=name, **k: _n)(fn))


def _install_transport_wrappers() -> None:
    from bajutsu.common.backend_cli import adb_resident
    from bajutsu.common.drivers import xcuitest

    raw_factory = xcuitest._raw_http_transport  # noqa: SLF001  # the seam under test

    def factory(host: str, port: int) -> Any:
        transport = raw_factory(host, port)
        return _timed("transport", lambda method, path, body=None: f"{method} {path}")(transport)

    xcuitest._raw_http_transport = factory  # noqa: SLF001
    adb_resident.fetch_source = _timed("transport", lambda *a, **k: "GET /source")(
        adb_resident.fetch_source
    )
    adb_resident.act = _timed("transport", lambda *a, **k: "POST /act")(adb_resident.act)
    adb_resident.fetch_clock = _timed("transport", lambda *a, **k: "GET /clock")(
        adb_resident.fetch_clock
    )
    # The three reassignments above never reach a production run: `ResidentServer.__init__` takes
    # `fetch`/`clock`/`act_probe` as keyword-only defaults bound to `fetch_source`/`fetch_clock`/`act`
    # at class-definition (import) time, before this function runs, and every real caller (`_begin_
    # resident`) takes those defaults rather than passing the module functions explicitly. Wrap the
    # `ResidentChannel` `start()` actually returns instead, so the timed calls are the ones a run uses.
    original_start = adb_resident.ResidentServer.start

    def start(self: Any) -> Any:
        channel = original_start(self)
        return adb_resident.ResidentChannel(
            fetch=_timed("transport", lambda *a, **k: "GET /source")(channel.fetch),
            clock=_timed("transport", lambda *a, **k: "GET /clock")(channel.clock),
            act=_timed("transport", lambda *a, **k: "POST /act")(channel.act),
        )

    adb_resident.ResidentServer.start = start  # type: ignore[method-assign]


def _install_evidence_wrappers() -> None:
    from bajutsu.common.evidence import core

    def name_of(self: Any, driver: Any, step_id: str, kinds: Any, *a: Any, **k: Any) -> str:
        return "capture " + ",".join(kinds)

    core.FileSink.capture = _timed("evidence", name_of)(core.FileSink.capture)  # type: ignore[method-assign]


def _step_kind(step: Any) -> str:
    fields = step.model_dump(exclude_none=True, by_alias=True)
    for key in fields:
        if key not in {"name", "capture", "extract", "from", "timeout"}:
            return str(key)
    return "step"


def _install_step_wrapper() -> None:
    from bajutsu.common.orchestrator import loop

    runner = loop._StepRunner  # noqa: SLF001  # the per-step entry point
    original = runner._run_one  # noqa: SLF001

    def run_one(self: Any, step: Any, active_driver: Any) -> Any:
        stack: list[str] = getattr(_LOCAL, "stack", None) or []
        _LOCAL.stack = stack
        key = f"{len(_STEPS)}:{_step_kind(step)}"
        stack.append(key)
        t0 = time.perf_counter()
        try:
            return original(self, step, active_driver)
        finally:
            stack.pop()
            with _LOCK:
                _STEPS.append({"step": key, "wall_s": round(time.perf_counter() - t0, 6)})

    runner._run_one = run_one  # noqa: SLF001


def _summarize(out: Path | None) -> None:
    per_step: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    by_name: dict[str, list[float]] = defaultdict(list)
    for r in _RECORDS:
        key = f"{r['category']}:{r['name']}"
        by_name[key].append(r["s"])
        if r["step"] is not None:
            per_step[r["step"]][r["category"]] += r["s"]
            counts[r["step"]][key] += 1
    lines = [
        "",
        "== per step (seconds) ==",
        f"{'step':<22}{'wall':>8}{'driver':>8}{'evid.':>8}{'subproc':>9}  driver-call counts",
    ]
    for s in _STEPS:
        cats = per_step[s["step"]]
        top = ", ".join(
            f"{k.split(':', 1)[1]}={v}"
            for k, v in counts[s["step"]].most_common()
            if k.startswith("driver:")
        )
        lines.append(
            f"{s['step']:<22}{s['wall_s']:>8.3f}{cats['driver']:>8.3f}{cats['evidence']:>8.3f}"
            f"{cats['subprocess']:>9.3f}  {top}"
        )
    lines += ["", "== by call (count, total s, mean ms) =="]
    for key, vals in sorted(by_name.items(), key=lambda kv: -sum(kv[1]))[:40]:
        lines.append(
            f"{key:<48}{len(vals):>6}{sum(vals):>9.3f}{sum(vals) / len(vals) * 1000:>9.1f}"
        )
    sys.stdout.write("\n".join(lines) + "\n")
    if out is not None:
        out.write_text(json.dumps({"steps": _STEPS, "records": _RECORDS}, indent=1))
        sys.stdout.write(f"trace written to {out}\n")


def main() -> None:
    argv = sys.argv[1:]
    out: Path | None = None
    if argv[:1] == ["--out"]:
        out = Path(argv[1])
        argv = argv[2:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    _install_subprocess_wrappers()
    _install_driver_wrappers()
    _install_transport_wrappers()
    _install_evidence_wrappers()
    _install_step_wrapper()
    atexit.register(_summarize, out)
    from bajutsu.cli import app

    sys.argv = ["bajutsu", *argv]
    app()


if __name__ == "__main__":
    main()
