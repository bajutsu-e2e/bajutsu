"""Measure the orchestrator's own per-step cost and count driver round trips per step kind.

Runs `run_scenario` over a FakeDriver that (a) counts every Driver call and (b) optionally sleeps a
modeled latency per call, so the same scenario can be projected onto an iOS / Android cost model.
No device needed. Usage: `uv run python bench_orchestrator.py [--model zero|ios|android_resident|android_dump]`.
"""

from __future__ import annotations

import argparse
import functools
import json
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence.core import FileSink, NullSink
from bajutsu.common.orchestrator.loop import run_scenario
from bajutsu.common.orchestrator.types import AlertGuardConfig, RealClock
from bajutsu.common.scenario import Scenario

# Modeled per-call latencies (seconds). `ios` and `android_resident` are `driver:*` means measured
# with `trace_run.py` on 2026-09-03 (iPhone 17 Pro Simulator, iOS 26.5 / Pixel-class emulator,
# `bajutsu-api34-arm64`, API 34 — see roadmaps/BE-XXXX-step-latency-driver-internal-tuning/misc/step-performance/README.md §7.4) against
# `demos/showcase/scenarios/controls.yaml`; unmeasured methods keep their original estimate.
MODELS: dict[str, dict[str, float]] = {
    "zero": {},
    "ios": {
        "query": 0.067,  # measured: transport GET /elements mean 62.7ms, driver:query 67.3ms
        "tap": 0.749,  # measured: driver:tap mean (transport POST /tap 690.2ms + resolve/settle)
        "type_text": 0.30,
        "screenshot": 0.090,  # measured: transport GET /screenshot mean
        "system_alert_labels": 0.15,  # SpringBoard snapshot
        "is_tappable": 0.10,
        "drain_interruptions": 0.001,  # measured: transport POST /interruptionPolicy/drain mean
        "drain_actuations": 0.0,
        "dismiss_blocking_tip": 0.0,
        "swipe": 0.30,
        "long_press": 0.5,
    },
    "android_resident": {
        "query": 0.263,  # measured: transport GET /source mean 262.9ms, driver:query 263.3ms
        "tap": 2.401,  # measured: driver:tap mean, almost entirely transport POST /act (2203.5ms)
        "type_text": 0.30,
        "screenshot": 0.104,  # measured: subprocess adb exec-out screencap mean
        "system_alert_labels": 0.0,
        "is_tappable": 0.0,
        "drain_interruptions": 0.0,
        "drain_actuations": 0.0,
        "swipe": 0.70,
        "long_press": 0.6,
    },
    "android_dump": {
        "query": 2.4,
        "tap": 0.20,
        "type_text": 0.40,
        "screenshot": 0.50,
        "swipe": 0.70,
        "long_press": 0.6,
    },
}

DRIVER_METHODS = [
    "query",
    "tap",
    "tap_point",
    "double_tap",
    "long_press",
    "swipe",
    "scroll",
    "viewport",
    "back",
    "type_text",
    "delete_text",
    "select_all",
    "copy_selection",
    "is_tappable",
    "wait_for",
    "screenshot",
    "system_alert_labels",
    "drain_interruptions",
    "dismiss_blocking_tip",
    "drain_actuations",
    "handle_system_alert",
    "set_interruption_policy",
    "settled_query",
]


def make_screen(n: int, label_seed: int = 0) -> list[base.Element]:
    els: list[base.Element] = [
        {
            "identifier": f"row-{i}",
            "label": f"Row {i} v{label_seed}",
            "traits": ["button"] if i % 3 == 0 else ["staticText"],
            "value": None,
            "frame": (0.0, float(i * 44), 390.0, 44.0),
        }
        for i in range(n)
    ]
    els.append(
        {
            "identifier": "go",
            "label": "Go",
            "traits": ["button"],
            "value": None,
            "frame": (0.0, 0.0, 100.0, 44.0),
        }
    )
    els.append(
        {
            "identifier": "field",
            "label": "Field",
            "traits": ["textField"],
            "value": None,
            "frame": (0.0, 50.0, 300.0, 44.0),
        }
    )
    return els


class CountingDriver(FakeDriver):
    """FakeDriver that counts calls and sleeps a modeled latency per call."""

    def __init__(self, model: dict[str, float], **kw: Any) -> None:
        super().__init__(**kw)
        self.calls: Counter[str] = Counter()
        self.modeled_s = 0.0
        self.model = model
        self.version = 0
        for name in DRIVER_METHODS:
            orig = getattr(self, name, None)
            if orig is None:
                continue
            setattr(self, name, self._wrap(name, orig))

    def _wrap(self, name: str, fn: Any) -> Any:
        @functools.wraps(fn)
        def wrapper(*a: Any, **k: Any) -> Any:
            self.calls[name] += 1
            cost = self.model.get(name, 0.0)
            if cost:
                time.sleep(cost)
                self.modeled_s += cost
            return fn(*a, **k)

        return wrapper


def react(driver: FakeDriver, kind: str, arg: object) -> None:
    # Every tap flips the screen to a new "version" so screenChanged / settled see a change.
    if kind == "tap":
        d = driver  # type: ignore[assignment]
        d.version = getattr(d, "version", 0) + 1  # type: ignore[attr-defined]
        driver.screen = make_screen(len(driver.screen) - 2, label_seed=d.version)  # type: ignore[attr-defined]


def scenarios(n: int) -> dict[str, Scenario]:
    tap = [{"tap": {"id": "go"}} for _ in range(n)]
    tap_wait = []
    for _ in range(n):
        tap_wait.append({"tap": {"id": "go"}})
        tap_wait.append({"wait": {"for": {"id": "row-1"}, "timeout": 5}})
    settled = [{"wait": {"until": "settled", "timeout": 5}} for _ in range(n)]
    tap_settled = []
    for _ in range(n):
        tap_settled.append({"tap": {"id": "go"}})
        tap_settled.append({"wait": {"until": "settled", "timeout": 5}})
    asserts = [{"assert": [{"exists": {"id": "row-2"}}]} for _ in range(n)]
    typing = [{"type": {"text": "hello", "into": {"id": "field"}}} for _ in range(n)]
    return {
        "tap": Scenario.model_validate({"name": "tap", "steps": tap}),
        "tap+wait_for": Scenario.model_validate({"name": "tw", "steps": tap_wait}),
        "settled": Scenario.model_validate({"name": "settled", "steps": settled}),
        "tap+settled": Scenario.model_validate({"name": "ts", "steps": tap_settled}),
        "assert": Scenario.model_validate({"name": "assert", "steps": asserts}),
        "type_into": Scenario.model_validate({"name": "type", "steps": typing}),
    }


def run_one(
    name: str, sc: Scenario, model: dict[str, float], *, file_sink: bool, guard: bool, tree: int
) -> dict[str, Any]:
    driver = CountingDriver(model, screen=make_screen(tree), react=react)
    tmp = Path(tempfile.mkdtemp(prefix="bajutsu-bench-"))
    try:
        sink = FileSink(tmp / "run") if file_sink else NullSink()
        capture = ["screenshot.after", "elements", "actionLog"] if file_sink else []
        t0 = time.perf_counter()
        result = run_scenario(
            driver,
            sc,
            RealClock(),
            sink=sink,
            alert_guard=AlertGuardConfig() if guard else None,
            capture=capture,
        )
        wall = time.perf_counter() - t0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    steps = len(sc.steps)
    assert result.failure is None, (name, result.failure)
    return {
        "scenario": name,
        "steps": steps,
        "wall_ms_per_step": round(wall / steps * 1000, 1),
        "modeled_driver_ms_per_step": round(driver.modeled_s / steps * 1000, 1),
        "orchestrator_ms_per_step": round((wall - driver.modeled_s) / steps * 1000, 1),
        "calls_per_step": {k: round(v / steps, 2) for k, v in sorted(driver.calls.items())},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="zero", choices=sorted(MODELS))
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--tree", type=int, default=300, help="elements in the fake screen")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    model = MODELS[args.model]
    rows = []
    for name, sc in scenarios(args.steps).items():
        for file_sink in (False, True):
            for guard in (False, True):
                r = run_one(name, sc, model, file_sink=file_sink, guard=guard, tree=args.tree)
                r["sink"] = "file" if file_sink else "null"
                r["guard"] = guard
                rows.append(r)
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    print(f"model={args.model} tree={args.tree} elements, steps={args.steps}")
    print(
        f"{'scenario':<14}{'sink':<6}{'guard':<7}{'wall/step':>10}{'driver':>8}{'orch':>8}  calls/step"
    )
    for r in rows:
        calls = " ".join(f"{k}={v}" for k, v in r["calls_per_step"].items())
        print(
            f"{r['scenario']:<14}{r['sink']:<6}{r['guard']!s:<7}"
            f"{r['wall_ms_per_step']:>9.1f}ms{r['modeled_driver_ms_per_step']:>8.0f}"
            f"{r['orchestrator_ms_per_step']:>8.1f}  {calls}"
        )


if __name__ == "__main__":
    main()
