"""`bajutsu serve` — a local web UI to author scenarios and run them.

A Tier-1 convenience (authoring / operation), **never part of the CI gate**.  Two top-level
tabs over the CLI: **Record** authors a scenario from a natural-language goal (``python -m
bajutsu record …``), streaming the agent's turn-by-turn progress and writing the result under
the scenarios dir; **Replay** runs a scenario (``python -m bajutsu run …``) and shows its
self-contained ``report.html``.  Each request spawns the CLI on a background thread, streams
its output, and the produced ``runs/<id>/`` tree is served so the report's relative asset
links resolve.  Stdlib only — the same ``ThreadingHTTPServer`` approach as the network
collector ([[network]]).

Split into three submodules:

* **helpers** — pure query/command-builder functions (no server state)
* **jobs** — ``Job``/``ServeState`` dataclasses and the run/cancel lifecycle
* **handler** — HTTP request handler, ``make_server``, and the embedded SPA
"""

from __future__ import annotations

from pathlib import Path

from bajutsu.serve.handler import make_server
from bajutsu.serve.helpers import (
    _int,
    _scenario_path,
    app_build_info,
    list_apps,
    list_runs,
    list_scenarios,
    list_simulators,
    record_command,
    run_command,
    scenario_out_path,
    unique_scenario_path,
)
from bajutsu.serve.jobs import Job, Popen, ServeState, cancel_job, run_job

__all__ = [
    "Job",
    "Popen",
    "ServeState",
    "_int",
    "_scenario_path",
    "app_build_info",
    "cancel_job",
    "list_apps",
    "list_runs",
    "list_scenarios",
    "list_simulators",
    "make_server",
    "record_command",
    "run_command",
    "run_job",
    "scenario_out_path",
    "serve",
    "unique_scenario_path",
]


def serve(host: str, port: int, scenarios_dir: Path, config: Path, runs_dir: Path) -> None:
    state = ServeState(scenarios_dir=scenarios_dir, config=config, runs_dir=runs_dir)
    server = make_server(state, host, port)
    bound = server.server_address[1]
    print(f"bajutsu serve → http://{host}:{bound}  (scenarios: {scenarios_dir} · Ctrl-C to stop)")  # noqa: T201
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")  # noqa: T201
    finally:
        server.shutdown()
        server.server_close()
