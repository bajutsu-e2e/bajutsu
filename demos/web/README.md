# Web demo on the Playwright backend

[日本語](README.ja.md)

This demo drives a tiny static web app with Bajutsu's **Playwright** backend ([BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)).
Unlike the iOS demos, it needs **no Mac and no Simulator** and runs on Linux inside the same
toolchain as `make check`.

## What's here

| Path | Purpose |
|---|---|
| `app/index.html` | the app under test — onboarding → login → counter, a Sync button that POSTs to `/api/sync` (the network lane's request), plus a device-verification flow for the handoff demo, vanilla JS, stable `data-testid` ids |
| `app/help.html` | a page nothing in `index.html` links to, reachable only by its address — what `scenarios/navigate.yaml` opens |
| `scenarios/smoke.yaml` | the deterministic smoke scenario (same step/expect schema as the iOS demos) |
| `scenarios/navigate.yaml` | opens `help.html` with a `navigate` step and returns by a relative reference ([BE-0464](../../roadmaps/BE-0464-web-navigate-step/BE-0464-web-navigate-step.md)) |
| `scenarios/html_attribute.yaml` | asserts the home screen's `docs.link` `href`, then taps `panel.load` and waits for the panel's `data-state` to settle from `loading` to `ready` with `aria-busy` removed ([BE-0466](../../roadmaps/BE-0466-web-attribute-assertion/BE-0466-web-attribute-assertion.md)) |
| `scenarios/network.yaml` | the network smoke ([BE-0282](../../roadmaps/BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)) — a mocked, captured `POST /api/sync` carrying a secret; tagged `network` so the default `e2e` (`--no-network`) skips it |
| `network/assert_redaction.py` | checks the persisted `network.json` masks the Sync request's secret — the redaction gap the run grammar can't assert |
| `record/goals.txt` | the natural-language goal `make -C demos/web record` authors from |
| `record/record_offline.py` | the offline, API-key-free twin of `record` — the same loop, a keyword agent + FakeDriver |
| `record/record_handoff_offline.py` | the offline, key-free twin of the human-in-the-loop handoff demo — the real pause/resume with a scripted agent + responder |
| `demo.config.yaml` | `targets.web` with `platform: web` + `subject.url` + `paths.scenarios` + `evidence.redact` (no `subject.id`) |
| `codegen/smoke.spec.ts` | the checked-in Playwright test **generated** from `scenarios/smoke.yaml` — the codegen real-compile gate's fixture ([BE-0293](../../roadmaps/BE-0293-codegen-playwright-real-compile/BE-0293-codegen-playwright-real-compile.md)), the web twin of the iOS `ComponentsUITests.swift` |
| `codegen/package.json` · `codegen/playwright.config.ts` | the `@playwright/test` runner (pinned to the Python `web` extra's Playwright version) and its config — serves `app/` on the spec's baked-in port |
| `Makefile` | `web-deps` / `app-serve` / `e2e` / `e2e-network` / `codegen-e2e` / `record` / `record-handoff` / `record-offline` / `record-handoff-offline` |

## Run it

```bash
make -C demos/web e2e
```

The command installs the web backend (`uv sync --extra web` + `playwright install chromium`), serves
`app/` on `127.0.0.1:8787`, runs the smoke scenario through the Playwright backend, and tears the
server down. The run is fully deterministic: pass/fail comes from the scenario's machine
assertions alone (the counter reads `2`), never an LLM.

`e2e` sets `BAJUTSU_MIN_WAIT_TIMEOUT` to `E2E_WAIT_FLOOR` (default `10` seconds), as the Android
lane does. A step's `assert` then polls within that floor, which `scenarios/html_attribute.yaml`
needs, because the panel's `data-state` reads `loading` for about 400 ms after the tap. The poll
returns as soon as the assertions pass, so the floor is an upper bound, not a fixed delay. Override
it with `make -C demos/web e2e E2E_WAIT_FLOOR=<seconds>`.

To poke the app by hand (or point the Web UI at it), run `make -C demos/web app-serve` and open
<http://127.0.0.1:8787/index.html>.

## Network smoke (BE-0282)

The default `e2e` above runs with `--no-network`, so it never exercises the real network path. The
network smoke does the opposite. It drives page.route interception, `requestfinished` capture, the
`mocked` provenance flag, and redaction of really-captured evidence:

```bash
make -C demos/web e2e-network
```

The command serves `app/`, taps the **Sync account** button (a real `POST /api/sync` carrying an
`Authorization` header and a `password` body field), and runs the `network`-tagged
[`scenarios/network.yaml`](scenarios/network.yaml) **with network on**. A [`mocks:`](scenarios/network.yaml)
entry answers the POST with `201` (a live server, if one existed, would `404`). A captured
exchange with status `201` proves that the mock, not the network, served it.

The scenario's `request` assertion is the deterministic interception/capture check. Then
[`network/assert_redaction.py`](network/assert_redaction.py) reads the persisted `network.json` and
fails unless the exchange meets every condition below:

- the exchange is `mocked`,
- the status is `201`,
- the redactor masked both secrets,
- the request's non-secret `account` field is still present with its value.

Without the last condition, a redactor that replaced the whole body, or masked every value in it,
would pass. No LLM appears anywhere: the verdict is the assertion plus that check.

This lane is the web half of [BE-0282](../../roadmaps/BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md).
It runs in CI as the `network (playwright)` job, which landed as signal first. Having proven
stable in CI, the job is now promoted into the required `E2E (web)` gate (BE-0282). **Android now has a
counterpart** ([BE-0283](../../roadmaps/BE-0283-android-network-capture/BE-0283-android-network-capture.md)):
`network (adb)` in `android-e2e.yml` captures real emulator traffic through BajutsuAndroid's app-side
interceptor, which reports to a host collector over `adb reverse`. That transport differs from
Playwright's in-browser interception here, but the verdict comes from the same `request` assertion,
and the Android job gates just as this job now does.

## Codegen real-compile gate (BE-0293)

`e2e` above drives the app through Bajutsu's *own* Playwright backend at runtime and never touches
codegen output. `bajutsu codegen --emit playwright` is a separate path. It turns a scenario into a
standalone `@playwright/test` file that a team runs in their own Playwright CI, with no Bajutsu
runtime and no AI. `tests/test_codegen_playwright.py` checks the emitted source as a string. That
test cannot check codegen's actual claim, that the emitted file is a real, runnable native test.
This gate closes that gap:

```bash
make -C demos/web codegen-e2e
```

The gate re-generates [`codegen/smoke.spec.ts`](codegen/smoke.spec.ts) from
[`scenarios/smoke.yaml`](scenarios/smoke.yaml) and runs the fresh output with the real
`@playwright/test` runner against a real Chromium. The runner transpiles the TypeScript and executes
it, so the check is a compile *and* a run, not a `tsc --noEmit` syntax check. The gate then **fails
if the output drifts from the checked-in fixture**, so the emitter and the fixture never silently
diverge. It needs Node/npm only (the runner is the destination framework, not our Python backend),
with no Simulator and no macOS.

The gate is the web twin of the iOS `codegen (xcuitest)` gate (`demos/showcase`'s `ui-test`, which
builds and runs a generated `ComponentsUITests.swift` with `xcodebuild test`). In CI it is the
`codegen (playwright)` job in `web-e2e.yml`. The job lands as **signal** first, reporting but not
blocking a merge, and will be promoted into the required `E2E (web)` gate once stable, exactly as
`network (playwright)` was ([BE-0293](../../roadmaps/BE-0293-codegen-playwright-real-compile/BE-0293-codegen-playwright-real-compile.md)).

## Record it

`record` (Tier 1) is the authoring path. AI reads a natural-language goal plus the live screen and
writes out the deterministic scenario that `run` later replays with no AI. This path is the web twin
of `make -C demos record`, and only the backend differs (a browser, not a Simulator).

```bash
make -C demos/web record          # real Claude drives the Playwright backend (needs ANTHROPIC_API_KEY)
make -C demos/web record GOAL="Get started, then increment the counter three times and confirm it shows 3."
```

The command serves `app/`, drives real Claude toward the goal in [`record/goals.txt`](record/goals.txt),
and writes the authored scenario to a gitignored `tmp/` file. The scenario holds the same clean
id-based selectors as `scenarios/smoke.yaml`, because the app exposes stable `data-testid` ids.
`record` is the only web path that needs an API key. The deterministic `run`/`e2e` above needs none.

With no key and no browser, the offline twin reproduces the *same* record loop, with the real
`Observation → Proposal` protocol and the emitted scenario. A deterministic keyword agent grounds
each step in an in-memory FakeDriver, so the twin runs in the `make check` toolchain:

```bash
make -C demos/web record-offline                                   # the default goal
uv run python demos/web/record/record_offline.py "get started, increment twice, check the counter shows 2"
```

## Human-in-the-loop handoff (BE-0179)

Some steps are gated by something the AI cannot supply, such as a one-time verification code that
arrives out-of-band. `record` pauses on such a step, hands off to a human, takes the human's response,
and resumes by re-observing the live screen ([BE-0179](../../roadmaps/BE-0179-record-human-handoff/BE-0179-record-human-handoff.md)).
The human is in the loop only **while authoring**. The recorded scenario still replays with no human
on the deterministic `run` path.

Run the demo **headed** so you can operate the browser when the AI pauses:

```bash
make -C demos/web record-handoff   # real Claude + a headed browser (needs ANTHROPIC_API_KEY)
```

The AI clicks **Verify a device**, reaches the code screen, recognizes that it cannot know the code
(an `ask_human` turn), and pauses. Enter the code shown in the browser, click **Verify**, then answer
the terminal prompt (`done`) to resume. The loop re-observes the verified screen and finishes.

With no key, no browser, and no live human, the offline twin reproduces the *same* pause/resume, with
the real handoff contract and record loop driven by a scripted agent and responder. The mechanism
runs in the `make check` toolchain:

```bash
make -C demos/web record-handoff-offline
```

## How it maps to the core

The web app exposes `data-testid` attributes, the web equivalent of iOS accessibilityIdentifier.
A scenario's `{ id: counter.increment }` selector resolves through the **same**
`resolve_unique` / `find_all` determinism core as every other backend. The Playwright driver changes
only *which* attribute satisfies the selector (`data-testid`) and *how* the tap is delivered
(coordinate-click the resolved frame center). See [drivers → Playwright](../../docs/drivers.md#playwright-web).
