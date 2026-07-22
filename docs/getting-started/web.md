**English** · [日本語](../ja/getting-started/web.md)

# Getting started — web track (no Mac required)

> Finishes the [Getting started](index.md) loop on **any operating system**, against a real web app
> in a browser via the **Playwright** backend — no macOS, no Xcode, no iOS Simulator. Have a Mac
> too? The [iOS track](ios.md) finishes the same loop on a Simulator instead.

Related: [Getting started](index.md) · [iOS track](ios.md) · [drivers](../drivers.md) · [glossary](../glossary.md)

Complete [Steps 1–3 of the shared walkthrough](index.md) first (install, unit tests, read a
scenario) — Step 1 there covers the base install; this page adds the one extra line the web
backend needs. This page picks up at Step 4.

## Why a web track

Bajutsu's headline claim is **"a platform is a backend"**: the deterministic core, the scenario
format, and the reporter are identical regardless of which backend actuates the UI. The
[iOS track](ios.md) finishes on a Simulator (the `XCUITest` backend), which needs a Mac with Xcode. This
track finishes the same loop against a browser (the `web` / Playwright backend), which runs on
Linux, Windows, or macOS alike — the exact backend that drives Bajutsu's own web demo on the Linux
gate ([`demos/web`](../../demos/web/README.md)).

The vocabulary here follows the [glossary](../glossary.md): a **backend** is the token you pass to
`--backend` (here `web`), it resolves to an **actuator** (the Playwright engine that performs the
taps and types), and a **target** is one `targets.<name>` config entry describing the app under
test — for the web, identified by a `baseUrl` rather than an iOS `bundleId`.

## What you'll need

| For… | You need |
|---|---|
| Steps 1–3 (shared) | any OS, Python 3.13 (managed via [uv](https://github.com/astral-sh/uv)) |
| Steps 4–5 below | `uv sync --extra web` + `uv run playwright install chromium` (no Mac / Simulator) |

```bash
uv sync --extra web                        # adds the Playwright python package
uv run playwright install chromium         # downloads the Chromium binary Playwright drives
```

The `--extra web` line and the `playwright install` line are what the web backend adds on top of
the base install from [Step 1](index.md#step-1--install) — the Chromium binary is a separate
download, not a pip dependency.

## Step 4 — Serve the demo web app

The web demo ships a tiny static app under [`demos/web/app`](../../demos/web/README.md) — onboarding →
login → counter, written in vanilla JavaScript with stable `data-testid` ids. Serve it locally so a
browser (and Bajutsu) can reach it:

```bash
make -C demos/web app-serve        # serves demos/web/app on 127.0.0.1:8787 (Ctrl-C to stop)
```

Leave it running and open <http://127.0.0.1:8787/index.html> in your own browser to poke the app by
hand — Get started, sign in with any email/password, and increment the counter. That is exactly the
flow the demo's scenario automates:

```yaml
scenarios:
  - name: onboard, log in, and increment the counter
    steps:
      - tap: { id: onboarding.start }
      - type: { text: "a@b.com", into: { id: auth.email } }
      - type: { text: "pw", into: { id: auth.password } }
      - tap: { id: auth.submit }
      - wait: { for: { id: home.title }, timeout: 5 }   # condition wait, never a fixed sleep
      - tap: { id: counter.increment }
      - tap: { id: counter.increment }
    expect:
      - exists: { id: home.title }
      - value: { sel: { id: counter.value }, equals: "2" }
```

This uses **exactly the same step/expect grammar** as [the iOS track's smoke test](index.md#step-3--read-a-scenario)
— steps act, `expect` judges, selectors are id-first (here `data-testid`, the web equivalent of
iOS's `accessibilityIdentifier`, through the **same** selector-resolution core as every other
backend — [selectors](../selectors.md), [drivers](../drivers.md#playwright-web)), waits are
conditions, and an ambiguous selector fails immediately. Only the specific scenario differs from
track to track — see [`demos/web/scenarios/smoke.yaml`](../../demos/web/scenarios/smoke.yaml) for
the full file.

## Step 5 — Run a scenario in the browser

The one-shot path is the `make` target: it installs the web backend if needed, serves the app in
the background, runs the demo's scenarios through Playwright, and tears the server down — no manual
serve required.

```bash
make -C demos/web e2e
```

Or drive the CLI directly against the app you served in Step 4 (the same run, written out):

```bash
uv run bajutsu run --scenario demos/web/scenarios/smoke.yaml --target web --backend web --config demos/web/demo.config.yaml
```

What the flags mean:

- `--target web` selects `targets.web` from [`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml)
  (its `baseUrl` and scenarios dir). The tool itself is app-agnostic; all per-target differences
  live in config ([configuration](../configuration.md)).
- `--backend web` picks the actuator: the Playwright engine driving Chromium. No Xcode, no
  Simulator is involved.
- Omit `--scenario` to run the target's whole scenarios directory (what `make -C demos/web e2e`
  does).

On success you'll see a line like:

```
PASS  runs/20260610-120000/manifest.json
```

`run` **exits 0 when every scenario passes, 1 on any failure**, and that exit code is the CI
(continuous integration) gate ([run-loop](../run-loop.md)). The verdict is purely the scenario's
machine assertions (the counter reads `2`) — never an LLM.

> Hit an environment problem (Chromium not installed, the web extra missing)? Run
> `uv run bajutsu doctor --target web --config demos/web/demo.config.yaml` first — it prints a
> ✓/✗ checklist of what the web backend needs.

Continue to [Step 6 — Read the report](index.md#step-6--read-the-report) in the shared walkthrough
(open with `xdg-open` on Linux instead of `open`).

## Author with AI (web)

Let Claude explore the web app toward a goal and write the scenario for you (Tier 1). Put
`ANTHROPIC_API_KEY=sk-ant-…` in a `.env` file, then:

```bash
make -C demos/web record GOAL="Get started, then increment the counter three times and confirm it shows 3."
```

With no key and no browser, the offline twin reproduces the same record loop deterministically
(`make -C demos/web record-offline`). How the authoring loop works: [recording](../recording.md).

## Onboard your own web app

Point a new `targets.<name>` entry at your app's `baseUrl` and give its elements stable
`data-testid` ids — see [configuration](../configuration.md#onboarding-a-new-target).
