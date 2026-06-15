"""Run the newly-added scenario features end-to-end without a Simulator.

This drives the *real* pipeline (load -> select -> expand_components -> expand_data
-> run_scenario) against the in-memory FakeDriver — the same path the unit tests use —
and prints what each feature did. Run: `uv run python demos/features/run_demo.py`.
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import RunResult, run_scenario
from bajutsu.scenario import (
    Component,
    Scenario,
    expand_components,
    expand_data,
    load_component,
    load_scenarios,
    select_scenarios,
)

HERE = Path(__file__).parent


def _el(identifier: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": identifier,
        "traits": traits or [],
        "value": None,
        "frame": (0.0, 0.0, 100.0, 40.0),
    }


# A static screen carrying every id the demo scenarios touch.
_BUTTONS = ["onboarding.start", "auth.submit", "home.tab"]
_FIELDS = ["auth.email", "auth.password", "search.field", "home.title", "results.list"]
SCREEN: list[base.Element] = [_el(i, ["button"]) for i in _BUTTONS] + [_el(i) for i in _FIELDS]


def _driver() -> FakeDriver:
    return FakeDriver(screen=list(SCREEN))


def _load(name: str) -> list[Scenario]:
    return load_scenarios((HERE / name).read_text(encoding="utf-8"))


def _resolve_component(ref: str) -> Component:
    return load_component((HERE / ref).read_text(encoding="utf-8"))


def _verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _run(scenario: Scenario, **kw: object) -> RunResult:
    return run_scenario(_driver(), scenario, **kw)  # type: ignore[arg-type]


def demo_tags() -> None:
    print("\n=== Phase 1: tags + selection ===")
    scns = _load("tags.yaml")
    print("  all scenarios:    ", [s.name for s in scns])
    selected = select_scenarios(scns, ["smoke"], ["slow"])
    print("  --tag smoke --exclude slow ->", [s.name for s in selected])
    for s in selected:
        r = _run(s)
        print(f"    [{_verdict(r.ok)}] {s.name}")


def demo_shared_steps() -> None:
    print("\n=== Phase 2: parameterized shared steps (use) ===")
    scns = _load("shared_steps.yaml")
    expand_components(scns, _resolve_component)
    s = scns[0]
    print(f"  expanded to {len(s.steps)} steps; any `use` left? {any(st.use for st in s.steps)}")
    typed = [st.type.text for st in s.steps if st.type is not None]
    print("  substituted inputs:", typed)
    r = _run(s)
    print(f"  [{_verdict(r.ok)}] {s.name}")


def demo_data_driven() -> None:
    print("\n=== Phase 3: data-driven scenarios ===")
    scns = expand_data(_load("data_driven.yaml"), lambda ref: [])
    print(f"  expanded to {len(scns)} runs:")
    for s in scns:
        r = _run(s)
        print(f"    [{_verdict(r.ok)}] {s.name}")


def demo_secrets() -> None:
    print("\n=== Phase 4: secret variables ===")
    s = _load("secrets.yaml")[0]
    drv = FakeDriver(screen=list(SCREEN))
    r = run_scenario(drv, s, bindings={"secrets.PASSWORD": "S3cr3t!"})
    typed = [arg for kind, arg in drv.actions if kind == "type"]
    print(f"  [{_verdict(r.ok)}] {s.name}")
    print("  value the driver actually received:", typed)
    print("  scenario definition still holds the token:", s.steps[1].type.text)  # type: ignore[union-attr]


def demo_device() -> None:
    print("\n=== Phase 5: device control (setLocation / push) ===")

    class RecordingControl:
        def __init__(self) -> None:
            self.locations: list[tuple[float, float]] = []
            self.pushes: list[dict[str, object]] = []

        def set_location(self, lat: float, lon: float) -> None:
            self.locations.append((lat, lon))

        def push(self, payload: dict[str, object]) -> None:
            self.pushes.append(payload)

    ctrl = RecordingControl()
    s = _load("device.yaml")[0]
    r = run_scenario(_driver(), s, control=ctrl)
    print(f"  [{_verdict(r.ok)}] {s.name}")
    print("  setLocation calls:", ctrl.locations)
    print("  push payloads:    ", ctrl.pushes)
    # Without a control injected, the same step fails cleanly rather than crashing.
    bad = run_scenario(_driver(), s)
    print(f"  without control -> ok={bad.ok}, failure={bad.failure!r}")


if __name__ == "__main__":
    demo_tags()
    demo_shared_steps()
    demo_data_driven()
    demo_secrets()
    demo_device()
    print("\nAll feature demos ran against the FakeDriver (no Simulator needed).")
