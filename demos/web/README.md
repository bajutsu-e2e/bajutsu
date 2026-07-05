# Web demo (Playwright backend)

[日本語](README.ja.md)

A tiny static web app driven by Bajutsu's **Playwright** backend ([BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)).
Unlike the iOS demos this needs **no Mac and no Simulator** — it runs on Linux inside the same
toolchain as `make check`.

## What's here

| Path | Purpose |
|---|---|
| `app/index.html` | the app under test — onboarding → login → counter, vanilla JS, stable `data-testid` ids |
| `scenarios/smoke.yaml` | the deterministic smoke scenario (same step/expect schema as the iOS demos) |
| `demo.config.yaml` | `targets.web` with `baseUrl` + `scenarios` + `backend: [web]` (no `bundleId`) |
| `Makefile` | `web-deps` / `app-serve` / `e2e` |

## Run it

```bash
make -C demos/web e2e
```

This installs the web backend (`uv sync --extra web` + `playwright install chromium`), serves
`app/` on `127.0.0.1:8787`, runs the smoke scenario through the Playwright backend, and tears the
server down. The run is fully deterministic — pass/fail comes only from the scenario's machine
assertions (the counter reads `2`), never an LLM.

To poke the app by hand (or point the Web UI at it), `make -C demos/web app-serve` and open
<http://127.0.0.1:8787/index.html>.

## How it maps to the core

The web app exposes `data-testid` attributes — the web equivalent of iOS accessibilityIdentifier.
A scenario's `{ id: counter.increment }` selector resolves through the **same**
`resolve_unique` / `find_all` determinism core as every other backend; the Playwright driver only
changes *which* attribute satisfies the selector (`data-testid`) and *how* the tap is delivered
(coordinate-click the resolved frame center). See [drivers → Playwright](../../docs/drivers.md#playwright-web).
