# The 60-second tour (zero setup)

The whole Bajutsu story in one command — **no Simulator, no idb, no API key, no Mac**. It
runs anywhere the Python core runs (Linux, CI, a fresh clone) in a couple of seconds, and is
the fastest way to *see* what the tool does before committing to an on-device setup.

```bash
uv run python demos/tour/tour.py
```

It walks the same four phases as the on-device demo ([`demos/record/demo.sh`](../record/README.md)),
but against an in-memory [`FakeDriver`](../../bajutsu/drivers/fake.py) so nothing external is needed:

1. **Author** — a natural-language goal becomes a deterministic scenario (YAML). This drives
   the real [`record`](../../bajutsu/record.py) loop; only the "brain" is swapped for a
   keyword stand-in ([`KeywordAgent`](../record/generate_from_nl.py)) so it needs no API key.
2. **Execute** — [`run`](../../bajutsu/runner.py) replays it through the real pipeline,
   writing a genuine run directory: `manifest.json`, JUnit XML, and a self-contained
   **`report.html`** you can open in a browser. It **PASSES**.
3. **Modify** — change the expected counter to a wrong value → the deterministic check
   **FAILS** (a machine assertion caught it, not an LLM) → fix it → it **PASSES** again.
4. **Diagnose** — rename a selector so it no longer resolves (a selector that drifted out from
   under the test) → the run **FAILS** → [`triage`](../../bajutsu/triage.py) reads the failed
   run and diagnoses it: the category (`selector`) plus a *"did you mean `counter.increment`?"*
   fix lifted from the captured element tree → restore the selector → it **PASSES**.

Everything except the phase-1 brain is the production code path — the same orchestrator,
assertion engine, report writer, and heuristic triage that a real run uses. That is the point:
this isn't a toy reimplementation, it's the real pipeline with a fake device under it.

## What you get

- The generated scenario at `demos/tour/generated.yaml` (gitignored) — edit the goal in the
  script or the YAML directly and re-run.
- A real report per phase under `demos/tour/runs/<phase>/report.html` (gitignored) — open
  `runs/02-pass/report.html` to see the steps, screenshots, and assertions exactly as the
  Web UI renders them.

## Next step: the same story on a real device

This tour stops at the FakeDriver boundary on purpose. To watch the *identical* lifecycle with
**real Claude** authoring against a **booted Simulator** through the idb backend — and the full
evidence set (video, network logs, system-alert handling, visual regression) — see:

- [`demos/record/`](../record/README.md) — AI authoring → run → modify → triage, on-device.
- [`demos/features/`](../features/README.md) — the Web UI tour through every evidence type.

The map of all three demos is in [`demos/README.md`](../README.md).
