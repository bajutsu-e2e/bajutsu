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

A web (Playwright) run navigates to the app's `baseUrl`, but nothing brings that server up — the run
assumes it is already listening. This item adds `apps.<name>.launchServer`: a declarative way to
start the target server before a run, wait until it is ready, and tear it down afterwards. It is the
web analogue of the iOS `build` hook (which produces the `.app` before a run), but for a
long-running process.

## Motivation

iOS targets have an on-demand preparation hook: `apps.<name>.build` is a shell command `serve` runs
to produce `appPath` before a scenario when the binary is missing. The web backend has no
equivalent — so every web harness re-implements "start the server, poll until it answers, run, kill
it" by hand. The serve Web UI dogfood ([BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md))
is the sharp case: testing the WebUI *through* the WebUI needs a second serve instance running as
the target, which its `Makefile` started, polled, and trapped manually. `demos/web` does the same
for a static page. That orchestration belongs in config, so `bajutsu run` (and, through it, the Web
UI's Run button) is self-contained.

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

1. **Probe** `readyUrl` (default `baseUrl`) once. If it already answers (HTTP `< 400`), the server
   was started elsewhere — a `Makefile`, CI, a manual launch — so **reuse** it and never tear it
   down. This mirrors `build` skipping when the binary already exists.
2. Otherwise **start** `cmd` in its own process group (so teardown reaches the server's children),
   then **poll** `readyUrl` until it answers or `readyTimeout` elapses. A command that exits early,
   or a timeout, fails the run with a clear message and exits `2`.
3. Run the scenarios.
4. **Teardown** (in the run's `finally`): if *we* started it, `SIGTERM` the group (then `SIGKILL`
   after a grace); a reused server is left running.

This stays inside the prime directives. The lifecycle is deterministic infrastructure — a shell
command plus an HTTP readiness poll, **no LLM** — so the Tier-2 gate is unaffected. Readiness is a
**condition wait** (poll until the server answers), never a blind fixed `sleep`. Because `serve`
runs jobs by spawning `bajutsu run`, the WebUI's Run button inherits the behaviour for free, and the
web-UI dogfood recursion is clean: the outer run starts the inner serve on its own port, drives it,
and stops it. The `demos/serve-ui` `Makefile` `e2e` target collapses to a single `bajutsu run`.

`cmd` is arbitrary shell from config — the **same trust model as `build`**, which already runs
config-declared commands; the hosted, multi-tenant `serve` gates config behind admin control, so
this adds no new exposure there.

## Alternatives considered

* **Scenario-level server** — rejected: couples scenarios to infrastructure and breaks "the same
  scenario runs anywhere" (app-agnostic prime directive). The target is a property of the app, so it
  belongs on `apps.<name>`, like `baseUrl` and `build`.
* **Reuse `mockServer`** — rejected: `mockServer` stubs the dependencies the app *calls*;
  `launchServer` hosts the app *under test* itself. Different roles; conflating them muddies both.
  They do share a "managed process + readiness" shape, so a future change could route both through
  one mechanism.
* **A `servers:` list** — deferred (YAGNI): one server covers the dogfood and `demos/web`. Promote to
  a list when a real multi-process target (frontend + API) appears.
* **Leave it to the `Makefile`** — rejected: that is the status quo this item removes; every new web
  harness otherwise re-implements start/poll/teardown.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

* [BE-0058 — Dogfood the serve Web UI](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) — the
  motivating consumer.
* [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md).
* The iOS `build` / `appPath` on-demand preparation hook (`bajutsu/config.py`,
  `bajutsu/serve/jobs.py`).
* [DESIGN.md](../../../DESIGN.md) — determinism (condition waits, no fixed sleep) and the app-agnostic
  principle.
