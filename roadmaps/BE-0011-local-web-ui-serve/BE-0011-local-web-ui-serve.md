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

A small launcher that lists scenarios and apps, runs them with a single click, streams run logs, and displays the report in the browser (stdlib only). It is a Tier 1 convenience feature and is not part of the CI gate. It also serves as the foundation for a planned GUI editor (visual editing and element picker).

## Motivation

The CLI is the contract, but it is not the friendliest way to author and replay scenarios. Running one means remembering the right `run` / `record` flags, picking an app and a Simulator, building the app first if its binary is stale, and then opening the produced `report.html` by hand. That friction discourages quick iteration — exactly the loop (author → run → look at the report → adjust) the tool is meant to make cheap. A small launcher removes it: pick an app, pick a scenario, click, and watch the output stream and the report appear, with the run's evidence served so the report's relative asset links resolve. It stays a Tier-1 convenience — never part of the CI gate — and gives the planned GUI editor (BE-0013) and richer record flow somewhere to live.

## Detailed design

Implemented; see `bajutsu/serve/`. Always launch it with `make serve` (never `bajutsu serve` directly): the wrapper installs the idb backend's deps on demand (the idb client and `idb_companion`), which a bare `serve` skips — leaving runs to fail with `no available actuator`.

The server is stdlib only — a `ThreadingHTTPServer` bound to `127.0.0.1`, the same approach as the network collector — with the single-page app shell and its CSS/JS inlined from `bajutsu/templates/serve.*` into one self-contained HTML response (no separate `/static` routes). Two top-level tabs sit over the CLI:

* **Record** authors a scenario from a natural-language goal by spawning `python -m bajutsu record …`, streaming the agent's turn-by-turn progress, and writing the result under the selected app's configured scenarios dir (auto-named, never overwriting an existing file).
* **Replay** runs a scenario with `python -m bajutsu run …` and embeds the produced `report.html` on completion. A **History** list reopens past runs, and an **Approve** button promotes a captured screenshot into a `visual` baseline (`POST /api/approve`).

Each request spawns the CLI on a background thread (`run_job`): it boots the chosen Simulators in parallel, builds the app on demand when its binary is missing (config `apps.<name>.build`), then runs the command, capturing combined output line-by-line and parsing the produced `runs/<id>` from it. The job is cancellable. The spawned child inherits the serve process's environment with the venv `bin` prepended to `PATH` so the `idb` client resolves. App differences stay in config: the app dropdown, its scenarios dir, the build command, and `appPath` all come from `apps.<name>`, so the server itself is app-agnostic. `--config` is optional — a file browser confined to `--root` opens one from the UI.

Determinism is preserved because pass/fail is never decided here: the UI only shells out to the deterministic `run`, and the only AI in the loop is `record` (Tier 1) and the opt-in alert guard. The one secret the UI sets — `ANTHROPIC_API_KEY` — is held in memory for the session only, never written to disk, and inherited by spawned jobs.

## Alternatives considered

* **A heavier web framework (Flask / FastAPI) and a build-step front-end.** Rejected: a localhost authoring convenience does not justify the dependency weight or a separate asset pipeline. Stdlib `ThreadingHTTPServer` plus inlined templates keeps the tool installable from a fresh clone and matches the existing network collector.
* **A native desktop or in-process GUI.** Rejected: the browser is already universal, needs no extra toolkit, and renders the self-contained `report.html` the runner already produces — reusing it rather than reimplementing a report view.
* **Driving the device in-process instead of spawning the CLI.** Rejected: shelling out to the same `run` / `record` commands keeps the CLI the single contract, so the UI can never diverge from CI behavior, and it gives cancellation and isolation for free.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

`bajutsu/serve/`, [cli.md](../../docs/cli.md)
