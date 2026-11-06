**English** · [日本語](BE-0011-local-web-ui-serve-ja.md)

# BE-0011 — Local web UI (`bajutsu serve`)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0011](BE-0011-local-web-ui-serve.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0011") |
| Implementing PR | predates the per-PR history (squashed into the initial import; no single PR) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

`bajutsu serve` is a small browser launcher built on the Python standard library alone. It lists scenarios and apps, runs a scenario with a single click, streams the run log, and displays the report. It is a [Tier 1](../../docs/glossary.md#the-two-tiers) convenience feature and sits outside the CI gate. It also serves as the foundation for a planned GUI editor (visual editing and an element picker).

## Motivation

The CLI is the contract, but it is not the friendliest way to author and replay scenarios. A single run means remembering the right `run` / `record` flags, picking an app and a Simulator, and building the app first when its binary is stale. The author then opens the produced `report.html` by hand. That friction discourages quick iteration, which is exactly the loop the tool is meant to make cheap: author, run, look at the report, adjust.

A small launcher removes the friction. The author picks an app and a scenario, then clicks. The output streams, and the report appears. The launcher also serves the run's evidence, so the report's relative asset links resolve. It stays a Tier 1 convenience and never joins the CI gate. It also gives the planned GUI editor ([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)) and a richer record flow a place to live.

## Detailed design

The feature is implemented in `bajutsu/serve/`. Always launch it with `make serve`, never with `bajutsu serve` directly. The `make` wrapper installs the idb backend's dependencies on demand (the idb client and `idb_companion`). A bare `serve` skips that step, and runs then fail with `no available actuator`.

The server uses the standard library alone. It is a `ThreadingHTTPServer` bound to `127.0.0.1`, the same approach as the network collector. The single-page app shell, with its CSS and JS, is inlined from `bajutsu/templates/serve.*` into one self-contained HTML response. There are no separate `/static` routes. Two top-level tabs sit over the CLI:

* **Record** authors a scenario from a natural-language goal. It spawns `python -m bajutsu record …` and streams the agent's turn-by-turn progress. It then writes the result under the selected app's configured scenarios directory, auto-named, and never overwrites an existing file.
* **Replay** runs a scenario with `python -m bajutsu run …` and embeds the produced `report.html` on completion. A **History** list reopens past runs. An **Approve** button promotes a captured screenshot into a `visual` baseline (`POST /api/approve`).

Each request spawns the CLI on a background thread (`run_job`). The job proceeds in four steps:

1. It boots the chosen Simulators in parallel.
2. It builds the app on demand when the app's binary is missing (config `apps.<name>.build`).
3. It runs the command and captures the combined output line by line.
4. It parses the produced `runs/<id>` from that output.

The job is cancellable. The spawned child inherits the serve process's environment, with the venv `bin` prepended to `PATH` so that the `idb` client resolves.

App differences stay in config. The app dropdown, the app's scenarios directory, the build command, and `appPath` all come from `apps.<name>`, so the server itself is app-agnostic. The `--config` flag is optional: a file browser confined to `--root` opens a config file from the UI.

Determinism is preserved because this server never decides pass/fail. The UI only shells out to the deterministic `run`. The only AI in the loop is `record` (Tier 1) and the opt-in alert guard. The one secret the UI sets, `ANTHROPIC_API_KEY`, is held in memory for the session alone and is never written to disk. Spawned jobs inherit it.

## Alternatives considered

* **A heavier web framework (Flask / FastAPI) and a build-step front-end.** Rejected. A localhost authoring convenience does not justify the dependency weight or a separate asset pipeline. The standard-library `ThreadingHTTPServer` plus inlined templates keeps the tool installable from a fresh clone and matches the existing network collector.
* **A native desktop or in-process GUI.** Rejected. The browser is already universal and needs no extra toolkit. It also renders the self-contained `report.html` that the runner already produces, so the UI reuses that report instead of reimplementing a report view.
* **Driving the device in-process instead of spawning the CLI.** Rejected. Shelling out to the same `run` / `record` commands keeps the CLI the single contract, so the UI can never diverge from CI behavior. Spawning also gives cancellation and isolation for free.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/serve/`, [cli.md](../../docs/cli.md)
