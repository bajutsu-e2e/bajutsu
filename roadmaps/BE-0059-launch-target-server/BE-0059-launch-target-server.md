**English** · [日本語](BE-0059-launch-target-server-ja.md)

# BE-0059 — Bring up the target server for a run (`launchServer`)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0059](BE-0059-launch-target-server.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0059") |
| Implementing PR | [#169](https://github.com/bajutsu-e2e/bajutsu/pull/169) |
| Topic | Dogfood fixtures (web UI) |
| Origin | Dogfooding |
<!-- /BE-METADATA -->

## Introduction

A web (Playwright) run navigates to the app's `baseUrl`, but nothing brings that server up. The run assumes the server is already listening.

This item adds `apps.<name>.launchServer`, a declarative way to manage the target server around a run. It starts the server before the run, waits until the server is ready, and tears it down afterwards. It is the web analogue of the iOS `build` hook, which produces the `.app` before a run, but it manages a long-running process.

## Motivation

iOS targets have an on-demand preparation hook. `apps.<name>.build` is a shell command. When the binary is missing, `serve` runs it before a scenario to produce `appPath`. The web backend has no equivalent. Every web harness therefore re-implements "start the server, poll until it answers, run, kill it" by hand.

The serve Web UI dogfood ([BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md)) is the sharp case. Testing the Web UI *through* the Web UI needs a second serve instance running as the target. Its `Makefile` started that instance, polled it, and trapped its cleanup manually. `demos/web` does the same for a static page.

That orchestration belongs in config. Then `bajutsu run` is self-contained, and so is the Web UI's Run button, which goes through `bajutsu run`.

## Detailed design

A new optional block on the app:

```yaml
apps:
  webui:
    baseUrl: "http://127.0.0.1:8799/"
    backend: [web]
    launchServer:
      cmd: "uv run bajutsu serve --config demos/web/demo.config.yaml --root demos/serve-ui --port 8799"
      readyUrl: "http://127.0.0.1:8799/"   # default: baseUrl
      readyTimeout: 60                       # seconds; a condition wait, never a fixed sleep
      # optional: cwd, env
```

`run` wraps scenario execution in this lifecycle (`bajutsu/runner/launch_server.py`):

1. **Probe** `readyUrl` (default `baseUrl`) once. If it already answers (HTTP `< 400`), something else started the server: a `Makefile`, CI, or a manual launch. The run **reuses** the server and never tears it down. This mirrors `build`, which skips when the binary already exists.
2. Otherwise **start** `cmd` in its own process group, so that teardown reaches the server's children. Then **poll** `readyUrl` until it answers or `readyTimeout` elapses. A command that exits early, or a timeout, fails the run with a clear message and exit code `2`.
3. Run the scenarios.
4. **Teardown**, in the run's `finally`. If *we* started the server, send `SIGTERM` to the group, then `SIGKILL` after a grace period. A reused server keeps running.

The lifecycle stays inside the prime directives. It is deterministic infrastructure: a shell command plus an HTTP readiness poll, with **no LLM**. The Tier-2 gate is unaffected. Readiness is a **condition wait** (poll until the server answers), never a blind fixed `sleep`.

`serve` runs jobs by spawning `bajutsu run`, so the Web UI's Run button inherits the behavior for free. The web-UI dogfood recursion also stays clean. The outer run starts the inner serve on its own port, drives it, and stops it. The `demos/serve-ui` `Makefile` `e2e` target collapses to a single `bajutsu run`.

`cmd` is arbitrary shell from config. The trust model is the **same as `build`**, which already runs config-declared commands. The hosted, multi-tenant `serve` gates config behind admin control, so `launchServer` adds no new exposure there.

## Alternatives considered

* **A scenario-level server.** Rejected. It couples scenarios to infrastructure and breaks "the same scenario runs anywhere" (the app-agnostic prime directive). The target is a property of the app, so it belongs on `apps.<name>`, like `baseUrl` and `build`.
* **Reuse `mockServer`.** Rejected. `mockServer` stubs the dependencies that the app *calls*. `launchServer` hosts the app *under test* itself. The roles differ, and conflating them muddies both. They do share a "managed process plus readiness" shape, so a future change could route both through one mechanism.
* **A `servers:` list.** Deferred (YAGNI). One server covers the dogfood and `demos/web`. Promote it to a list when a real multi-process target (frontend plus API) appears.
* **Leave it to the `Makefile`.** Rejected. That is the status quo that this item removes. Otherwise every new web harness re-implements start, poll, and teardown.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

* [BE-0058 — Dogfood the serve Web UI](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md): the motivating consumer.
* [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md).
* The iOS `build` / `appPath` on-demand preparation hook (`bajutsu/config.py`, `bajutsu/serve/jobs.py`).
* [DESIGN.md](../../DESIGN.md): determinism (condition waits, no fixed sleep) and the app-agnostic principle.
