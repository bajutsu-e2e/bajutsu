**English** · [日本語](BE-0076-web-cross-browser-engines-ja.md)

# BE-0076 — Selectable browser engines & cross-browser compatibility matrix (web backend)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0076](BE-0076-web-cross-browser-engines.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

Give the Web (Playwright) backend a **browser-engine axis**. Two phases:

1. **Engine selection** — a `--browser chromium|firefox|webkit` flag (with a config default) so
   `record` and `run` drive a chosen engine instead of being hard-wired to Chromium.
2. **Cross-browser matrix** — a `--browsers chromium,firefox,webkit` fan-out that runs the same
   scenario(s) across several engines and emits a deterministic **engine × scenario pass/fail
   matrix**. A cell that fails on one engine while passing on another is surfaced as a
   machine-detected rendering-engine / spec incompatibility.

The browser engine is an **execution axis** — like `--workers` or device selection — not scenario
content, so scenarios stay engine-neutral and platform-neutral. No LLM enters the gate: each
per-engine result is the existing deterministic `run` verdict, and the matrix is pure aggregation
of those verdicts. The proposal sits entirely inside the prime directives ([CLAUDE.md](../../../CLAUDE.md)).

## Motivation

### The backend is Chromium-only today

The Web backend that landed with [BE-0041](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)
is hard-wired to one engine: `bajutsu/drivers/playwright.py` starts the browser through
`_start_chromium`, which calls `pw.chromium.launch(...)`, and `bajutsu/backends.py`
`ensure_web_runtime` installs only the `chromium` browser. BE-0041's own seam table promised a
"headless, **cross-browser**" actuator, but the shipped v1 slice reaches only Chromium/Blink. The
engine is not exposed anywhere the user can choose it.

### Engine-specific breakage is exactly an E2E concern

Playwright bundles three real, independent rendering engines — Chromium (Blink), Firefox (Gecko),
and WebKit (the engine behind Safari) — and **all three run headless on Linux**, inside the
existing `make check` / CI gate, needing no Mac and no extra infrastructure. The bugs they expose
are the ones a single-engine test can never see: a CSS feature or layout quirk that only Gecko
honours, a JavaScript / DOM API that WebKit lacks or implements differently, a date / number input
that renders unequally, a flexbox or grid edge case. "Works in Chrome, broken in Safari" is a
classic production incident; an E2E tool that only ever drives Chromium is structurally blind to
it. Detecting that class of failure is squarely what an E2E tool is for, and Playwright makes it
nearly free here because the alternative engines are a download, not a device farm.

### It fits the prime directives cleanly

The detection is **machine-checkable, not an AI judgment**. "The scenario passes on Chromium and
fails on WebKit" is two deterministic `run` verdicts — each computed from machine assertions alone,
exactly as today. The matrix is a table of those verdicts; nothing in the gate consults a model, so
prime directive #1 (AI never judges) holds by construction. AI's role is unchanged and advisory:
`triage` ([BE-0021](../../implemented/BE-0021-ai-triage/BE-0021-ai-triage.md)) can *investigate*
why only WebKit failed, but never decides pass/fail. Determinism (#2) is preserved because each
per-engine run is an independent deterministic run with condition waits, never a fixed sleep. And
because the engine is an execution axis carried by a flag/config and never written into the
scenario, app-agnosticism (#3) holds: the same scenario YAML runs unchanged on every engine.

## Detailed design

### Phase 1 — engine selection

**The axis.** A new `--browser <engine>` option on `run` and `record`, where `<engine>` is one of
`chromium` (default, preserving today's behaviour), `firefox`, or `webkit`. A matching config
default lets a target pin its engine without a flag (e.g. an `apps.<name>.browser` key resolved by
`config.py`, defaulting to `chromium`). The flag overrides the config; the config overrides the
built-in default — the same precedence the existing web options follow.

**Threading it through the one seam.** Engine selection touches only the web backend's
construction path; the deterministic core is untouched.

| Seam | Today | Change |
|---|---|---|
| `bajutsu/drivers/playwright.py` | `_start_chromium(headless)` → `pw.chromium.launch(...)` | `_start_browser(engine, headless)` selecting `pw.chromium` / `pw.firefox` / `pw.webkit`; `PlaywrightDriver` takes a `browser` argument |
| `bajutsu/backends.py` `make_driver` | `PlaywrightDriver(base_url, headless=headless)` | also forward the resolved `browser` |
| `bajutsu/backends.py` `ensure_web_runtime` | `playwright install chromium` | install the requested engine(s) on demand (`chromium` / `firefox` / `webkit`) |
| `pyproject.toml` / CI | Chromium only | optionally install firefox + webkit for the cross-engine job |

`record` drives whichever engine is selected (it is AI authoring against a driver — no extra work
beyond passing the flag through). The selector mapping, the `query()` DOM walk, the
resolve-through-the-core actuation, and `capabilities()` are engine-independent and stay as they
are; the QUERY_JS snapshot is standard DOM and runs identically on all three engines.

### Phase 2 — cross-browser matrix

**The fan-out.** A `--browsers <list>` option on `run` (e.g. `--browsers chromium,firefox,webkit`)
runs each selected scenario once per listed engine. This reuses the parallel-lane machinery from
[BE-0054](../../implemented/BE-0054-web-backend-completion/BE-0054-web-backend-completion.md): a `BrowserContext`
is a near-free "device", so each (scenario, engine) pair is an independent lane in the device pool's
web branch. `--browsers chromium` is exactly `--browser chromium`; the two options are the
single-engine and multi-engine spellings of one axis.

**The deliverable — an engine × scenario matrix.** The report (`manifest.json` + `report.html`)
gains an engine dimension: every (scenario, engine) cell carries that run's deterministic verdict
and its evidence (kept in per-engine subdirectories so artifacts never collide). The matrix view
makes engine-specific breakage legible at a glance — a row green on Chromium and Firefox but red on
WebKit is the machine-detected incompatibility this item exists to find. JUnit output keys results
by engine (engine as a suite/classname axis) so CI sees per-engine cases.

**Verdict semantics.** A `--browsers` run is green only if **every** requested engine passes every
selected scenario; any engine-specific failure fails the run. This keeps the cross-browser run a
genuine deterministic gate rather than advisory reporting. (Whether an engine can be marked
"advisory / non-blocking" — to track a known WebKit gap without failing CI — is a possible refinement,
noted under Alternatives, not v1.)

### Determinism, app-agnosticism, and the gate

* **No LLM, no verdict touched by a model.** Each per-engine result is the existing deterministic
  `run`; the matrix aggregates verdicts. Prime directives #1 and #2 hold by construction.
* **App-agnostic.** The engine is an execution axis (flag/config), never scenario content; the same
  YAML runs on every engine, so prime directive #3 holds.
* **Linux-testable, inside the existing gate.** All three engines run headless on Linux, so the
  cross-engine path is exercised by the same `make check` / web-e2e CI job as the current Chromium
  path — no Mac, no emulator. The one real cost is CI download/cache of the Firefox and WebKit
  browser builds for the cross-engine job (Chromium-only runs are unaffected).

### The test contract (machine-checkable)

The `demos/web` scenarios already run deterministically on the web backend in CI. This item extends
that net: run a representative scenario under each engine and assert the per-engine verdicts, and add
a fixture that deliberately depends on an engine-divergent behaviour to prove the matrix actually
flags an engine-specific failure (red on one engine, green on the others) rather than silently
passing.

### Relationship to existing items

* **Builds on [BE-0041](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)** (the
  web backend) and **reuses [BE-0054](../../implemented/BE-0054-web-backend-completion/BE-0054-web-backend-completion.md)'s
  parallel lanes** for the fan-out. It is *distinct* from BE-0054, whose scope is explicitly the
  rich-end capabilities (native network, video/console evidence, emulated multi-touch, parallel
  runs) on a single engine — it does not address engine selection or a cross-engine matrix. This
  item adds the engine axis on top.
* **[BE-0021](../../implemented/BE-0021-ai-triage/BE-0021-ai-triage.md) (AI triage)** extends
  naturally: a failure seen on only one engine is a strong, structured hint for the advisory
  root-cause investigation — still never a verdict.
* **[BE-0062](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md)
  (Playwright codegen)** could later emit per-engine project configuration; out of scope here.

## Alternatives considered

* **Put the engine in the scenario YAML.** Rejected: it would make a scenario engine-bound and break
  the platform/engine-neutral scenario model — the same YAML must run on iOS, Chromium, Firefox, and
  WebKit unchanged. The engine belongs on the execution axis, exactly like `--workers` and device
  selection, not in the artifact under test.
* **Encode the engine in the backend token (`--backend web:firefox`).** Rejected as the primary
  surface: the registry expands a *platform* token to an *actuator* (`web` → `playwright`), and a
  rendering engine is neither a platform nor a separate actuator — it is a parameter to the one
  Playwright actuator. A dedicated `--browser` flag is clearer, mirrors Playwright's own CLI, and
  keeps the registry's platform→actuator meaning intact. The `web:firefox` spelling could be added
  later as sugar if there is demand.
* **Stay Chromium-only and rely on manual cross-browser checks.** Rejected: that is exactly the gap
  this item closes. Engine-specific breakage is what an E2E tool should catch, and Playwright makes
  the alternative engines nearly free on the existing Linux gate, so leaving it manual forfeits the
  cheapest cross-engine coverage available.
* **Cross-engine *visual* parity (pixel-diff a screenshot across engines).** Deliberately a non-goal
  for v1. Rendering legitimately differs between engines (font hinting, sub-pixel layout), so a
  cross-engine pixel diff is noisy and risks drifting toward "does it look the same", which is not a
  clean machine verdict. This item is about **functional** pass/fail per engine. Per-engine visual
  baselines via the existing visual-regression assertion
  ([BE-0029](../../implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md))
  remain available as a separate, opt-in path.
* **Mark some engines "advisory / non-blocking".** A reasonable refinement (track a known WebKit gap
  without failing CI), but it adds verdict-policy surface; v1 keeps the simple all-must-pass rule and
  leaves per-engine blocking policy to a follow-up.

## References

* [CLAUDE.md](../../../CLAUDE.md), [DESIGN.md](../../../DESIGN.md) — the prime directives this
  respects: AI never judges (the matrix aggregates deterministic verdicts), determinism first
  (per-engine condition-waited runs), app-agnostic (engine is an execution axis, not scenario content).
* [BE-0041 — Web (Playwright) backend](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)
  — the backend this extends, and its "cross-browser" seam promise.
* [BE-0054 — Web backend completion](../../implemented/BE-0054-web-backend-completion/BE-0054-web-backend-completion.md)
  — the parallel-lane pool this fan-out reuses; distinct (single-engine rich capabilities) in scope.
* [BE-0021 — AI triage](../../implemented/BE-0021-ai-triage/BE-0021-ai-triage.md) — advisory
  investigation of an engine-specific failure.
* [BE-0029 — Visual-regression assertions](../../implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md),
  [BE-0062 — Playwright codegen](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md)
  — adjacent, out-of-scope follow-ups.
* `bajutsu/drivers/playwright.py` (`_start_chromium`), `bajutsu/backends.py` (`make_driver`,
  `ensure_web_runtime`), `bajutsu/runner/pool.py` (the web lane branch) — the seams this changes;
  [drivers.md](../../../docs/drivers.md), [multi-platform.md](../../../docs/multi-platform.md).
