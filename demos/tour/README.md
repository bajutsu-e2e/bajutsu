# The tour — run, modify, diagnose on a real Simulator

**English** · [日本語](README.ja.md)

The whole Bajutsu lifecycle in one command, on a real iOS Simulator — and **fully
deterministic: no LLM, no API key**. `run` and `triage` never use AI, so the only thing you
need is a Mac with a booted Simulator (idb auto-installs).

```bash
make -C demos tour                   # or, directly:
./demos/tour/demo.sh
```

It runs against the dedicated [`demo` app](../app/README.md) (onboarding → login → counter) and
walks three phases:

1. **Run** — execute the committed scenario [`counter.yaml`](../app/scenarios/counter.yaml) on
   the Simulator. It writes a real run directory (manifest, JUnit, `report.html`) and **PASSES**.
2. **Modify** — change the expected counter to a wrong value → the deterministic check **FAILS**
   (a machine assertion caught it, not an LLM) → fix it → it **PASSES** again.
3. **Diagnose** — rename a selector so it no longer resolves (a selector that drifted out from
   under the test) → the run **FAILS** → [`triage`](../../bajutsu/triage.py) reads the failed run
   and diagnoses it: the category (`selector`) plus a *"did you mean `counter.increment`?"* fix
   lifted from the captured element tree → restore the selector → it **PASSES**.

The script edits a gitignored working copy (`demos/tour/scenario.yaml`), never the tracked
`counter.yaml`, so the repo stays clean. The only difference from the on-device `record` demo is
that the scenario is already authored — there's no Claude step, so no API key.

## No Mac? The same story on a fake device (zero setup)

The identical author → run → modify → diagnose lifecycle also runs against an in-memory
[`FakeDriver`](../../bajutsu/drivers/fake.py) — no Simulator, no idb, no API key, on Linux/CI in
seconds:

```bash
uv run python demos/tour/tour.py
```

This version additionally shows **authoring**: a natural-language goal becomes the scenario via
the real [`record`](../../bajutsu/record.py) loop, with a keyword stand-in for Claude
([`KeywordAgent`](../record/generate_from_nl.py)) so it needs no key. Everything except that
stand-in brain is the production code path — the same orchestrator, assertion engine, report
writer, and heuristic triage. It writes a real `report.html` under `demos/tour/runs/` you can
open. It's the fastest first look; the on-device `make -C demos tour` above is the real thing on
a device.

## Status

> The `demo` app and `counter.yaml` were authored against the iOS Simulator's accessibility
> model and validated by replaying the scenario through the real pipeline on a fake driver, but
> have **not yet been built and run on-device** — confirm with `make -C demos tour` on a Mac.

## Where to go next

- [`demos/features/`](../features/WEBUI.md) — the Web UI tour through every evidence type.
- [`demos/record/`](../record/README.md) — AI authoring with real Claude on-device.
- The map of all demos: [`demos/README.md`](../README.md).
