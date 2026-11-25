**English** · [日本語](BE-0076-web-cross-browser-engines-ja.md)

# BE-0076 — Selectable browser engines & cross-browser compatibility matrix (web backend)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0076](BE-0076-web-cross-browser-engines.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0076") |
| Implementing PR | [#355](https://github.com/bajutsu-e2e/bajutsu/pull/355), [#360](https://github.com/bajutsu-e2e/bajutsu/pull/360) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

This proposal gives the Web (Playwright) backend a **browser-engine axis** in two phases:

1. **Engine selection.** A `--browser chromium|firefox|webkit` flag (with a config default) lets `record` and `run` drive a chosen engine. Today both are hard-wired to Chromium.
2. **Cross-browser matrix.** A `--browsers chromium,firefox,webkit` fan-out runs the same scenario(s) across several engines. It emits a deterministic **engine × scenario pass/fail matrix**. A cell that fails on one engine while passing on another surfaces as a machine-detected rendering-engine or spec incompatibility.

The browser engine is an **execution axis**, like `--workers` or device selection. It is not scenario content, so scenarios stay engine-neutral and platform-neutral. No large language model (LLM) enters the gate. Each per-engine result is the existing deterministic `run` verdict, and the matrix purely aggregates those verdicts. The proposal sits entirely inside the prime directives ([CLAUDE.md](../../CLAUDE.md)).

## Motivation

### The backend is Chromium-only today

The Web backend that landed with [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) is hard-wired to one engine:

- `bajutsu/drivers/playwright.py` starts the browser through `_start_chromium`, which calls `pw.chromium.launch(...)`.
- `bajutsu/backends.py` `ensure_web_runtime` installs the `chromium` browser and no other.

BE-0041's own seam table promised a "headless, **cross-browser**" actuator. The shipped v1 slice reaches only Chromium/Blink. The engine is not exposed anywhere the user can choose it.

### Engine-specific breakage is exactly an E2E concern

Playwright bundles three real, independent rendering engines: Chromium (Blink), Firefox (Gecko), and WebKit (the engine behind Safari). **All three run headless on Linux**, inside the existing `make check` / CI gate. They need no Mac and no extra infrastructure.

The bugs they expose are the ones that a single-engine test can never see:

- a CSS feature or layout quirk that only Gecko honours
- a JavaScript or DOM API that WebKit lacks or implements differently
- a date or number input that renders unequally
- a flexbox or grid edge case

"Works in Chrome, broken in Safari" is a classic production incident. An E2E tool that only ever drives Chromium is structurally blind to it. Detecting that class of failure is squarely what an E2E tool is for. Playwright makes the detection nearly free here, because the alternative engines are a download and not a device farm.

### It fits the prime directives cleanly

The detection is **machine-checkable, not an AI judgment**. "The scenario passes on Chromium and fails on WebKit" is two deterministic `run` verdicts. Each verdict comes from machine assertions alone, exactly as today. The matrix is a table of those verdicts, and nothing in the gate consults a model. Prime directive #1 (AI never judges) therefore holds by construction.

AI's role is unchanged and advisory. `triage` ([BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage.md)) can *investigate* why only WebKit failed, but it never decides pass/fail.

Determinism (#2) is preserved. Each per-engine run is an independent deterministic run with condition waits and never a fixed sleep. App-agnosticism (#3) holds too. A flag or config carries the engine as an execution axis, and the scenario never records it. The same scenario YAML runs unchanged on every engine.

## Detailed design

### Phase 1 — engine selection

**The axis.** A new `--browser <engine>` option on `run` and `record` takes one of three values: `chromium` (the default, which preserves today's behaviour), `firefox`, or `webkit`. A matching config default lets a target pin its engine without a flag. The key is `targets.<name>.browser`, which `config.py` resolves into `Effective`, defaulting to `chromium`. The precedence mirrors the existing `headless` / `--headed` knob exactly: flag > config > built-in default.

`config.py` already carries a web-only field that is the precise template here. `TargetConfig.headless` (`bool = True`) resolves into `Effective.headless`. When `--headed/--no-headed` is given, `run` overrides it with `replace(eff, headless=not headed)`. The `browser` axis adds the same three pieces in lockstep:

1. **Config field.** `TargetConfig.browser: str = "chromium"` is web-only, and iOS ignores it. A `field_validator` checks it against `{"chromium", "firefox", "webkit"}`, so a typo fails at load time. This is the loud-at-load pattern that `Defaults._valid_idb_version` uses, and it avoids a crash mid-run.
2. **Resolution into `Effective`.** A `browser: str = "chromium"` field on the frozen `Effective` dataclass is populated in `resolve()` straight from `a.browser`. It is a per-target knob like `headless`, not a `defaults`-merged one.
3. **Flag override.** A `--browser <engine>` option on `run` / `record` applies `eff = replace(eff, browser=<engine>)` when set. The shape is exactly that of today's `--headed` override. The option validates against the same three-engine set, so an unknown value exits 2 and never reaches Playwright.

**Threading it through the one seam.** Engine selection touches only the web backend's construction path. The deterministic core is untouched: `base.resolve_unique` / `find_all`, the `query()` snapshot, and the orchestrator. Today the engine is fixed three layers down. `PlaywrightDriver.__init__` defaults `starter: Starter = _start_chromium`, where `Starter = Callable[[bool], _Started]`. `_start_chromium(headless)` calls `pw.chromium.launch(...)`. The change generalizes that one closure over the engine name.

| Seam | Today | Change |
|---|---|---|
| `bajutsu/drivers/playwright.py` | `_start_chromium(headless)` → `pw.chromium.launch(headless=…, slow_mo=…)`; `PlaywrightDriver(…, starter=_start_chromium)` | a `_start_browser(engine)` factory returning a `Starter` that launches `getattr(pw, engine)` (`pw.chromium` / `pw.firefox` / `pw.webkit`); `PlaywrightDriver` takes a `browser: str = "chromium"` argument and builds its starter from it, so `relaunch()` (which re-invokes `self._starter`) rebuilds the *same* engine |
| `bajutsu/backends.py` `make_driver` | `make_driver(actuator, udid, *, base_url, headless, record_video_dir)` → `PlaywrightDriver(base_url, headless=headless, …)` | add a `browser: str = "chromium"` keyword and forward it: `PlaywrightDriver(base_url, headless=headless, browser=browser, …)` |
| `bajutsu/runner/launch.py` `launch_driver` | calls `make_driver(actuator, udid, base_url=eff.base_url, headless=eff.headless, …)` | also pass `browser=eff.browser` (the one call site that builds a web driver for a run; `doctor._current_screen` constructs `PlaywrightDriver` directly and gains the same `browser=eff.browser`) |
| `bajutsu/backends.py` `ensure_web_runtime` | no-op when Playwright is already importable; only when web is requested *and* Playwright is missing does it `uv pip install playwright` + `playwright install chromium` | when it provisions a missing Playwright (and as a follow-on engine-install step regardless), install the requested engine(s) — `playwright install <engine>` for one, `playwright install firefox webkit` (plus chromium) for the matrix; see below |
| `.github/workflows/web-e2e.yml` / docs | `playwright install --with-deps chromium` | a cross-engine job installs `firefox webkit` too; the Playwright-browser cache key (already `hashFiles('uv.lock')`) is unchanged |

`record` drives whichever engine is selected. It is AI authoring against the same `Driver` interface, so passing `eff.browser` through `launch_driver` is the whole change. The recorded YAML stays engine-neutral. Several pieces are engine-independent and stay unchanged:

- the selector mapping (`_ROLE_MAP`)
- the `query()` DOM walk over `QUERY_JS`
- the resolve-through-the-core actuation
- the health signals (`pageerror` / main-frame status / `dialog`)
- `capabilities()`

`QUERY_JS` is standard DOM and `getBoundingClientRect` geometry, and it runs identically on all three engines.

**On-demand install.** Today `ensure_web_runtime(backends)` is a no-op unless a web backend is requested *and* the Playwright package is missing. It returns early when web is not requested or when `_playwright_available()` (an import probe) is already true. Only on the missing path does it `uv pip install playwright` and then `playwright install chromium`. The package probe does not distinguish *which browser binaries* are present. Installing firefox or webkit therefore needs a per-engine check, not the single package probe.

The design works as follows. After the package is ensured, run `playwright install <engines>` for the engines that this run needs: the resolved `eff.browser`, or the `--browsers` list. `playwright install` is **idempotent**. The web-e2e workflow comment already relies on this ("a stale browser is simply re-downloaded"). Calling it unconditionally for the requested engines is therefore safe. A missing binary is fetched, and a present one is a fast no-op. The auto-provisioning contract stays intact: `make serve` adds idb, and a web run adds Playwright. The design widens the contract to the chosen engine.

**`doctor` reporting.** `doctor` already gates runnability via `preflight.runnability(actuator, …)` and prints a fixable checklist. For the web actuator, it should report **which engines are installed**. A per-engine presence check does this, for example by probing `playwright install --dry-run` output or the browser registry path. The message "you asked for `webkit` but only `chromium` is installed" then surfaces in `doctor` with a one-line fix. It no longer appears as a confusing downstream launch failure. The idb-version check plays the same role for iOS.

**Implementation status.** Phase 1 (engine selection) has shipped. It covers:

- the `browser` config field and its load-time validation
- the `--browser` flag on `run` / `record`, with flag > config > default precedence
- the engine threaded through `PlaywrightDriver` / `make_driver` / the web environment / `doctor`
- the on-demand `playwright install <engine>` in `ensure_web_runtime`

The fast `make check` gate covers all of it with a fake starter and no real browser. On-device firefox / webkit launch falls to the web-e2e path.

**Phase 2 (the `--browsers` matrix) has shipped.** It covers:

- the `--browsers` flag on `run`
- the run-per-engine fan-out (`run_matrix_and_report`), which writes evidence under `run_dir/<engine>/<sid>`
- the engine-tagged `RunResult.engine`
- the manifest `matrix` block and the all-must-pass `ok`
- the engine-keyed JUnit `classname="bajutsu.<engine>"`
- the `report.html` engine × scenario grid

The fast gate covers all of it with fake leases. On-device cross-engine runs remain the web-e2e path.

### Phase 2 — cross-browser matrix

**The fan-out.** A `--browsers <list>` option on `run` (for example `--browsers chromium,firefox,webkit`) runs each selected scenario once per listed engine. `--browsers chromium` is exactly `--browser chromium`. The two options are the single-engine and multi-engine spellings of one axis. A single-engine run never pays for the matrix machinery.

**How the fan-out maps onto the run pipeline: sequential per engine.** The run is a **loop over engines**, and each engine gets a full `run_and_report`-shaped pass. The design avoids mixing engines inside one device pool, for a concrete reason. `device_pool` selects exactly one actuator (`select_actuator(backends)`) and builds one kind of lane. `_resolve_lanes` already turns `--workers N` into N near-free `web-{i}` `BrowserContext` lanes *for one engine*.

The design reuses the parallel lanes of [BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) **within** an engine, so `--workers` still parallelizes scenarios. It iterates engines **around** that pool. Each engine's pool stays homogeneous. No per-lane engine needs threading through `device_pool`, `launch_driver`, or the collector wiring.

Each engine pass leases its own pool and runs the selected scenarios via the existing `run_all`. It produces a per-engine result list and evidence tree. The matrix is the assembly of those passes.

A future optimization could run engines concurrently, because they are independent processes. Sequential-per-engine is the v1 choice. It reuses the existing single-engine path unchanged, and it keeps evidence directories trivially non-colliding.

**Evidence layout — no collisions.** Today `run_all` writes each scenario's artifacts under `run_dir/<sid>`, where `sid = f"{i:02d}-{scenario_slug(s.name)}"`. The matrix prefixes that path with the engine: `run_dir/<engine>/<sid>` (for example `chromium/00-login` and `webkit/00-login`). The same scenario on two engines never overwrites the other's `network.json`, screenshots, or video. Each engine pass gets its own `run_dir` subtree.

**The deliverable: an engine × scenario matrix in the manifest.** `manifest.json` is the run's single source of truth (`manifest_dict` → `"scenarios": [asdict(r) for r in results]`, `"ok": all(r.ok …)`). The matrix extends it without breaking the v1 shape. Each `RunResult` already carries a `backend` field. The natural representation is therefore **one flat list of results tagged by engine**, plus an aggregate matrix view. Concretely:

- Each per-engine `RunResult` records its engine. `RunResult.backend` is `"playwright"` for every web result today, so the design does not overload it. It adds an explicit `engine: str = ""` field (empty for iOS and single-engine runs), populated per pass. `backend` keeps meaning the actuator, and `engine` means the rendering engine.
- The manifest gains a top-level `"matrix"` block: `{ engines: ["chromium","firefox","webkit"], scenarios: [<name>…], cells: { "<scenario>": { "<engine>": {ok, sid, failure} } } }`. The block purely aggregates the per-engine verdicts already in `scenarios`. `report.html` renders it as the engine × scenario grid. A row that is green on Chromium and Firefox but red on WebKit is the machine-detected incompatibility this item exists to find.
- JUnit (`junit_xml`) today emits one `<testcase classname="bajutsu">` per scenario. The matrix keys the engine into the case: `classname="bajutsu.<engine>"` (or a per-engine `<testsuite>`). CI then sees `chromium.login` and `webkit.login` as distinct cases. A per-engine failure is attributable in the CI UI without reading the manifest.

**Verdict semantics: machine-only, all-must-pass.** A `--browsers` run is green only if **every** requested engine passes every selected scenario (`manifest["ok"] = all(r.ok for r in every engine's results)`). Any engine-specific failure fails the run. Each cell's `ok` is the existing deterministic `run` verdict: machine assertions only, condition-waited, no fixed sleep, **no LLM**. The matrix purely aggregates those booleans, so prime directive #1 holds by construction. Nothing in the gate consults a model.

Marking an engine "advisory / non-blocking" would track a known WebKit gap without failing CI. It is a possible refinement, noted under Alternatives, and it is not part of v1.

### Validation plan

The work splits cleanly across the two gates that the project already runs (CLAUDE.md "the gate").

**Fast `make check` gate (no browser, Linux, the bulk of the coverage).** Everything except the actual browser launch is browser-free. Fakes make it unit-testable, matching the existing web tests that drive `parse_dom` and an injected `_Page` without Playwright:

- *Engine resolution and precedence.* Assert that `resolve()` yields `Effective.browser == "chromium"` by default and honours `targets.<name>.browser`. Assert that the `--browser` override wins over config. This is the three-tier precedence, tested the same way as the `headless` / `--headed` precedence.
- *Validation.* An invalid `browser` value fails at config load (the `field_validator`). An invalid `--browser` flag exits 2. Neither check touches a browser.
- *Threading.* A fake `Starter` (the existing test seam, `PlaywrightDriver(starter=…)`) records the engine it was asked for. The record proves that `make_driver` / `launch_driver` forward `eff.browser` end to end.
- *Matrix assembly.* Feed the manifest and JUnit builders synthetic per-engine `RunResult`s (a green Chromium and a red WebKit for the same scenario). Assert the `"matrix"` block, the aggregate `ok`, the per-engine evidence prefixes, and the engine-keyed JUnit cases. All of this comes from data, with no browser. The test locks in the "red on one engine, green on another is surfaced" contract deterministically.

**Web-e2e path (real browsers, Linux, heavier, not in `make check`).** `web-e2e.yml` already drives a real headless Chromium against `demos/web`. The design extends it with a cross-engine job that does three things:

- It installs `firefox webkit`.
- It runs a representative `demos/web` scenario under each engine and asserts the per-engine verdicts.
- It adds one fixture that deliberately depends on an engine-divergent behaviour. The fixture proves end to end that the matrix flags a genuine engine-specific failure (red on one, green on the others) and does not pass silently.

A path filter gates this job, as it gates today's web-e2e, and the job is not a required check. It adds the real cross-engine signal without slowing the fast gate.

### Determinism, app-agnosticism, and the gate

* **No LLM, no verdict touched by a model.** Each per-engine result is the existing deterministic `run`, and the matrix aggregates the verdicts. Prime directives #1 and #2 hold by construction.
* **App-agnostic.** The engine is an execution axis (flag or config) and never scenario content. The same YAML runs on every engine, so prime directive #3 holds.
* **Linux-testable, inside the existing gate.** All three engines run headless on Linux. The same `make check` / web-e2e CI job that covers the current Chromium path therefore exercises the cross-engine path. The path needs no Mac and no emulator. The one real cost is the CI download and cache of the Firefox and WebKit browser builds for the cross-engine job. Chromium-only runs are unaffected.

### Relationship to existing items

* **Builds on [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)** (the web backend) and **reuses the parallel lanes of [BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md)** for the fan-out. It is *distinct* from BE-0054. BE-0054's scope is explicitly the rich-end capabilities on a single engine: native network, video/console evidence, emulated multi-touch, and parallel runs. BE-0054 does not address engine selection or a cross-engine matrix. This item adds the engine axis on top.
* **[BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage.md) (AI triage)** extends naturally. A failure seen on only one engine is a strong, structured hint for the advisory root-cause investigation. It is still never a verdict.
* **[BE-0062](../BE-0062-playwright-codegen/BE-0062-playwright-codegen.md) (Playwright codegen)** could later emit per-engine project configuration. That work is out of scope here.

## Alternatives considered

* **Put the engine in the scenario YAML.** Rejected: it would bind a scenario to an engine and break the platform/engine-neutral scenario model. The same YAML must run on iOS, Chromium, Firefox, and WebKit unchanged. The engine belongs on the execution axis, exactly like `--workers` and device selection. It does not belong in the artifact under test.
* **Encode the engine in the backend token (`--backend web:firefox`).** Rejected as the primary surface. The registry expands a *platform* token to an *actuator* (`web` → `playwright`). A rendering engine is neither a platform nor a separate actuator. It is a parameter to the one Playwright actuator. A dedicated `--browser` flag is clearer and mirrors Playwright's own CLI. It also keeps the registry's platform→actuator meaning intact. The `web:firefox` spelling could be added later as sugar if there is demand.
* **Stay Chromium-only and rely on manual cross-browser checks.** Rejected: that is exactly the gap this item closes. An E2E tool should catch engine-specific breakage. Playwright makes the alternative engines nearly free on the existing Linux gate. Leaving the check manual forfeits the cheapest cross-engine coverage available.
* **Cross-engine *visual* parity (pixel-diff a screenshot across engines).** Deliberately a non-goal for v1. Rendering legitimately differs between engines (font hinting, sub-pixel layout). A cross-engine pixel diff is therefore noisy, and it risks drifting toward "does it look the same", which is not a clean machine verdict. This item is about **functional** pass/fail per engine. The existing visual-regression assertion ([BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md)) still offers per-engine visual baselines as a separate, opt-in path.
* **Mark some engines "advisory / non-blocking".** A reasonable refinement: it would track a known WebKit gap without failing CI. It also adds verdict-policy surface. v1 keeps the simple all-must-pass rule and leaves per-engine blocking policy to a follow-up.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

* [CLAUDE.md](../../CLAUDE.md), [DESIGN.md](../../DESIGN.md) — the prime directives this
  respects: AI never judges (the matrix aggregates deterministic verdicts), determinism first
  (per-engine condition-waited runs), app-agnostic (engine is an execution axis, not scenario content).
* [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)
  — the backend this extends, and its "cross-browser" seam promise.
* [BE-0054 — Web backend completion](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md)
  — the parallel-lane pool this fan-out reuses; distinct (single-engine rich capabilities) in scope.
* [BE-0021 — AI triage](../BE-0021-ai-triage/BE-0021-ai-triage.md) — advisory
  investigation of an engine-specific failure.
* [BE-0029 — Visual-regression assertions](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md),
  [BE-0062 — Playwright codegen](../BE-0062-playwright-codegen/BE-0062-playwright-codegen.md)
  — adjacent, out-of-scope follow-ups.
* The seams this changes: `bajutsu/drivers/playwright.py` (`_start_chromium`, the `Starter` type,
  `PlaywrightDriver.__init__`), `bajutsu/backends.py` (`make_driver`, `ensure_web_runtime`,
  `capabilities_for`), `bajutsu/runner/launch.py` (`launch_driver`), `bajutsu/runner/pool.py` (the web
  lane branch + `_resolve_lanes`' `web-{i}` lanes), `bajutsu/config.py`
  (`TargetConfig.headless` → `Effective.headless` → `resolve`, the template the `browser` field
  follows), `bajutsu/cli/commands/run.py` / `record.py` (the `--headed` override the `--browser` flag
  mirrors), `bajutsu/report/manifest.py` (`manifest_dict`, `junit_xml`), and
  `.github/workflows/web-e2e.yml`; [drivers.md](../../docs/drivers.md),
  [multi-platform.md](../../docs/multi-platform.md).
