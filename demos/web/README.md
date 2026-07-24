# Web demo (Playwright backend)

[日本語](README.ja.md)

A tiny static web app driven by Bajutsu's **Playwright** backend ([BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)).
Unlike the iOS demos this needs **no Mac and no Simulator** — it runs on Linux inside the same
toolchain as `make check`.

## What's here

| Path | Purpose |
|---|---|
| `app/index.html` | the app under test — onboarding → login → counter, a Sync button that POSTs to `/api/sync` (the network lane's request), plus a device-verification flow for the handoff demo, vanilla JS, stable `data-testid` ids |
| `scenarios/smoke.yaml` | the deterministic smoke scenario (same step/expect schema as the iOS demos) |
| `scenarios/network.yaml` | the network smoke ([BE-0282](../../roadmaps/BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)) — a mocked, captured `POST /api/sync` carrying a secret; tagged `network` so the default `e2e` (`--no-network`) skips it |
| `network/assert_redaction.py` | checks the persisted `network.json` masks the Sync request's secret — the redaction gap the run grammar can't assert |
| `record/goals.txt` | the natural-language goal `make -C demos/web record` authors from |
| `record/record_offline.py` | the offline, API-key-free twin of `record` — the same loop, a keyword agent + FakeDriver |
| `record/record_handoff_offline.py` | the offline, key-free twin of the human-in-the-loop handoff demo — the real pause/resume with a scripted agent + responder |
| `demo.config.yaml` | `targets.web` with `baseUrl` + `scenarios` + `backend: [web]` (no `bundleId`) |
| `codegen/smoke.spec.ts` | the checked-in Playwright test **generated** from `scenarios/smoke.yaml` — the codegen real-compile gate's fixture ([BE-0293](../../roadmaps/BE-0293-codegen-playwright-real-compile/BE-0293-codegen-playwright-real-compile.md)), the web twin of the iOS `ComponentsUITests.swift` |
| `codegen/package.json` · `codegen/playwright.config.ts` | the `@playwright/test` runner (pinned to the Python `web` extra's Playwright version) and its config — serves `app/` on the spec's baked-in port |
| `Makefile` | `web-deps` / `app-serve` / `e2e` / `e2e-network` / `codegen-e2e` / `record` / `record-handoff` / `record-offline` / `record-handoff-offline` |

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

## Network smoke (BE-0282)

The default `e2e` above runs with `--no-network`, so it never exercises the real network path. The
network smoke does the opposite — it drives page.route interception, `requestfinished` capture, the
`mocked` provenance flag, and redaction of really-captured evidence:

```bash
make -C demos/web e2e-network
```

It serves `app/`, taps the **Sync account** button (a real `POST /api/sync` carrying an
`Authorization` header and a `password` body field), and runs the `network`-tagged
[`scenarios/network.yaml`](scenarios/network.yaml) **with network on**. A [`mocks:`](scenarios/network.yaml)
entry answers the POST with `201` (a live server, if one existed, would `404`), so a captured
exchange with status `201` proves the mock — not the network — served it. The scenario's `request`
assertion is the deterministic interception/capture check; then
[`network/assert_redaction.py`](network/assert_redaction.py) reads the persisted `network.json` and
fails unless the exchange is `mocked`, is `201`, and has both secrets masked. No LLM anywhere — the
verdict is the assertion plus that check.

This lane is the web half of [BE-0282](../../roadmaps/BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md).
It runs in CI as the `network (playwright)` job, which landed as signal first and, having proven
stable in CI, is now promoted into the required `E2E (web)` gate (BE-0282). **Android now has a
counterpart** ([BE-0283](../../roadmaps/BE-0283-android-network-capture/BE-0283-android-network-capture.md)):
`network (adb)` in `android-e2e.yml` captures real emulator traffic through BajutsuAndroid's app-side
interceptor reporting to a host collector over `adb reverse` — a different transport from Playwright's
in-browser interception here, but the same `request`-assertion verdict, and it gates there just as
this job now does.

## Codegen real-compile gate (BE-0293)

`e2e` above drives the app through Bajutsu's *own* Playwright backend at runtime — it never touches
codegen output. `bajutsu codegen --emit playwright` is a separate path that turns a scenario into a
standalone `@playwright/test` file a team runs in their own Playwright CI, with no Bajutsu runtime and
no AI. `tests/test_codegen_playwright.py` checks the emitted source as a string; what it cannot check
is codegen's actual claim — that the emitted file is a real, runnable native test. This gate closes
that gap:

```bash
make -C demos/web codegen-e2e
```

It re-generates [`codegen/smoke.spec.ts`](codegen/smoke.spec.ts) from
[`scenarios/smoke.yaml`](scenarios/smoke.yaml), runs the fresh output with the real
`@playwright/test` runner against a real Chromium — which transpiles the TypeScript and executes it,
so it is a compile *and* a run, not a `tsc --noEmit` syntax check — then **fails if the output drifts
from the checked-in fixture** (so the emitter and the fixture never silently diverge). Node/npm only
(the runner is the destination framework, not our Python backend); no Simulator, no macOS.

This is the web twin of the iOS `codegen (xcuitest)` gate (`demos/showcase`'s `ui-test`, which builds
and runs a generated `ComponentsUITests.swift` with `xcodebuild test`). In CI it is the
`codegen (playwright)` job in `web-e2e.yml`; it lands as **signal** first — reporting but not blocking
a merge — to be promoted into the required `E2E (web)` gate once stable, exactly as `network
(playwright)` was ([BE-0293](../../roadmaps/BE-0293-codegen-playwright-real-compile/BE-0293-codegen-playwright-real-compile.md)).

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

## Human-in-the-loop handoff (BE-0179)

Some steps are gated by something the AI cannot supply — here, a one-time verification code that
arrives out-of-band. `record` pauses on such a step, hands off to a human, takes their response, and
resumes by re-observing the live screen ([BE-0179](../../roadmaps/BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)).
The human is only in the loop **while authoring**; the recorded scenario still replays with no human
on the deterministic `run` path.

Run it **headed** so you can operate the browser when the AI pauses:

```bash
make -C demos/web record-handoff   # real Claude + a headed browser (needs ANTHROPIC_API_KEY)
```

The AI clicks **Verify a device**, reaches the code screen, recognizes it cannot know the code (an
`ask_human` turn), and pauses. Enter the code shown in the browser, click **Verify**, then answer the
terminal prompt (`done`) to resume — the loop re-observes the verified screen and finishes.

With no key, no browser, and no live human, the offline twin reproduces the *same* pause/resume — the
real handoff contract and record loop — with a scripted agent + responder, so the mechanism runs in
the `make check` toolchain:

```bash
make -C demos/web record-handoff-offline
```

## How it maps to the core

The web app exposes `data-testid` attributes — the web equivalent of iOS accessibilityIdentifier.
A scenario's `{ id: counter.increment }` selector resolves through the **same**
`resolve_unique` / `find_all` determinism core as every other backend; the Playwright driver only
changes *which* attribute satisfies the selector (`data-testid`) and *how* the tap is delivered
(coordinate-click the resolved frame center). See [drivers → Playwright](../../docs/drivers.md#playwright-web).
