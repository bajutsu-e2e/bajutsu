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
| `record/goals.txt` | the natural-language goal `make -C demos/web record` authors from |
| `record/record_offline.py` | the offline, API-key-free twin of `record` — the same loop, a keyword agent + FakeDriver |
| `demo.config.yaml` | `targets.web` with `baseUrl` + `scenarios` + `backend: [web]` (no `bundleId`) |
| `Makefile` | `web-deps` / `app-serve` / `e2e` / `record` / `record-offline` |

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

## Record it

`record` (Tier 1) is the authoring path: AI reads a natural-language goal plus the live screen and
writes out the deterministic scenario `run` later replays with no AI. This is the web twin of
`make -C demos record` — only the backend differs (a browser, not a Simulator).

```bash
make -C demos/web record          # real Claude drives the Playwright backend (needs ANTHROPIC_API_KEY)
make -C demos/web record GOAL="Get started, then increment the counter three times and confirm it shows 3."
```

It serves `app/`, drives real Claude toward the goal in [`record/goals.txt`](record/goals.txt), and
writes the authored scenario to a gitignored `tmp/` file — the same clean id-based selectors
`scenarios/smoke.yaml` holds, because the app exposes stable `data-testid` ids. `record` is the only
web path that needs an API key; the deterministic `run`/`e2e` above needs none.

With no key and no browser, the offline twin reproduces the *same* record loop — the real
`Observation → Proposal` protocol and emitted scenario — with a deterministic keyword agent grounding
each step in an in-memory FakeDriver, so it runs in the `make check` toolchain:

```bash
make -C demos/web record-offline                                   # the default goal
uv run python demos/web/record/record_offline.py "get started, increment twice, check the counter shows 2"
```

## How it maps to the core

The web app exposes `data-testid` attributes — the web equivalent of iOS accessibilityIdentifier.
A scenario's `{ id: counter.increment }` selector resolves through the **same**
`resolve_unique` / `find_all` determinism core as every other backend; the Playwright driver only
changes *which* attribute satisfies the selector (`data-testid`) and *how* the tap is delivered
(coordinate-click the resolved frame center). See [drivers → Playwright](../../docs/drivers.md#playwright-web).
