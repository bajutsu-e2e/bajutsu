# The tour: run, modify, diagnose on a real Simulator

**English** · [日本語](README.ja.md)

This tour runs the whole Bajutsu lifecycle in one command on a real iOS Simulator, and it is **fully
deterministic: no LLM, no API key**. `run` and `triage` never use AI, so all you need is a Mac with
Xcode and a booted Simulator.

```bash
make -C demos tour                   # or, directly:
./demos/tour/demo.sh
```

The tour runs against the [showcase](../showcase/README.md) app (the SwiftUI accessibility product)
and walks three phases:

1. **Run.** Execute the committed scenario [`tour.yaml`](../showcase/scenarios/menu/tour.yaml) on
   the Simulator: open the third horse from the Stable catalog and turn on Favorite. The run writes a
   real run directory (manifest, JUnit, `report.html`) and **PASSES**.
2. **Modify.** Change the expected favorite state to a wrong value, and the deterministic check
   **FAILS** (a machine assertion caught it, not an LLM). Fix the value, and the run **PASSES** again.
3. **Diagnose.** Rename a selector so it no longer resolves (a selector that drifted out from
   under the test), and the run **FAILS**. [`triage`](../../bajutsu/triage/heuristic.py) reads the
   failed run and diagnoses it: the category (`selector`) plus a *"did you mean `stable.row.3`?"* fix
   lifted from the captured element tree. Restore the selector, and the run **PASSES**.

The script edits a gitignored working copy (`demos/tour/scenario.yaml`) and never touches the tracked
`tour.yaml`, so the repo stays clean. The on-device `record` demo differs from the tour in one way:
the tour's scenario is already authored, so it has no Claude step and needs no API key.

## The same story on a fake device, with no Mac

The identical author → run → modify → diagnose lifecycle also runs against an in-memory
[`FakeDriver`](../../bajutsu/common/drivers/fake.py). It needs no Simulator, Xcode, or API key, and
finishes on Linux/CI in seconds:

```bash
uv run python demos/tour/tour.py
```

This version also shows **authoring**. A natural-language goal becomes the scenario through the real
[`record`](../../bajutsu/record/loop.py) loop, with a keyword stand-in for Claude
([`KeywordAgent`](../record/generate_from_nl.py)) so no key is needed. Everything except that
stand-in brain is the production code path: the same orchestrator, assertion engine, report
writer, and heuristic triage. The run writes a real `report.html` under `demos/tour/runs/` that you
can open. This version is the fastest first look, and the on-device `make -C demos tour` above is
the real thing on a device.

## Status

> `tour.yaml` reuses identifiers that the showcase smoke / search scenarios and the `record` goals
> already drive on-device (`stable.row.*`, `horse.favorite`), and it parses through the real
> pipeline. The tour flow itself has **not yet been replayed on-device** in this form, so
> confirm it with `make -C demos tour` on a Mac.

## Where to go next

- [`demos/showcase/WEBUI.md`](../showcase/WEBUI.md): the Web UI tour through every evidence type.
- [`demos/showcase/`](../showcase/README.md#run-on-a-booted-simulator): AI authoring with real Claude on-device (`record`).
- [`demos/README.md`](../README.md): the map of all demos.
