**English** · [日本語](BE-0076-web-cross-browser-engines-ja.md)

# BE-0076 — Selectable browser engines & cross-browser compatibility matrix (web backend)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0076](BE-0076-web-cross-browser-engines.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | [#355](https://github.com/bajutsu-e2e/bajutsu/pull/355), [#360](https://github.com/bajutsu-e2e/bajutsu/pull/360) |
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

The Web backend that landed with [BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)
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
default lets a target pin its engine without a flag: a `targets.<name>.browser` key, resolved by
`config.py` into `Effective`, defaulting to `chromium`. The precedence mirrors the existing
`headless` / `--headed` knob exactly — flag > config > built-in default.

`config.py` already carries a web-only field that is the precise template here: `TargetConfig.headless`
(`bool = True`) resolves into `Effective.headless`, and `run` overrides it with
`replace(eff, headless=not headed)` when `--headed/--no-headed` is given. The `browser` axis adds
the same three pieces in lockstep:

1. **Config field.** `TargetConfig.browser: str = "chromium"` (web-only, iOS ignores it), validated
   against `{"chromium", "firefox", "webkit"}` with a `field_validator` so a typo fails at load time
   — the same loud-at-load pattern `Defaults._valid_idb_version` uses, not a crash mid-run.
2. **Resolution into `Effective`.** A `browser: str = "chromium"` field on the frozen `Effective`
   dataclass, populated in `resolve()` straight from `a.browser` (a per-target knob, like `headless`,
   not a `defaults`-merged one).
3. **Flag override.** A `--browser <engine>` option on `run` / `record` that, when set, applies
   `eff = replace(eff, browser=<engine>)` — the exact shape of today's `--headed` override, validated
   to the same three-engine set so an unknown value exits 2 rather than reaching Playwright.

**Threading it through the one seam.** Engine selection touches only the web backend's
construction path; the deterministic core (`base.resolve_unique` / `find_all`, the `query()`
snapshot, the orchestrator) is untouched. Today the engine is fixed three layers down:
`PlaywrightDriver.__init__` defaults `starter: Starter = _start_chromium`, where
`Starter = Callable[[bool], _Started]` and `_start_chromium(headless)` calls `pw.chromium.launch(...)`.
The change generalizes that one closure over the engine name.

| Seam | Today | Change |
|---|---|---|
| `bajutsu/drivers/playwright.py` | `_start_chromium(headless)` → `pw.chromium.launch(headless=…, slow_mo=…)`; `PlaywrightDriver(…, starter=_start_chromium)` | a `_start_browser(engine)` factory returning a `Starter` that launches `getattr(pw, engine)` (`pw.chromium` / `pw.firefox` / `pw.webkit`); `PlaywrightDriver` takes a `browser: str = "chromium"` argument and builds its starter from it, so `relaunch()` (which re-invokes `self._starter`) rebuilds the *same* engine |
| `bajutsu/backends.py` `make_driver` | `make_driver(actuator, udid, *, base_url, headless, record_video_dir)` → `PlaywrightDriver(base_url, headless=headless, …)` | add a `browser: str = "chromium"` keyword and forward it: `PlaywrightDriver(base_url, headless=headless, browser=browser, …)` |
| `bajutsu/runner/launch.py` `launch_driver` | calls `make_driver(actuator, udid, base_url=eff.base_url, headless=eff.headless, …)` | also pass `browser=eff.browser` (the one call site that builds a web driver for a run; `doctor._current_screen` constructs `PlaywrightDriver` directly and gains the same `browser=eff.browser`) |
| `bajutsu/backends.py` `ensure_web_runtime` | no-op when Playwright is already importable; only when web is requested *and* Playwright is missing does it `uv pip install playwright` + `playwright install chromium` | when it provisions a missing Playwright (and as a follow-on engine-install step regardless), install the requested engine(s) — `playwright install <engine>` for one, `playwright install firefox webkit` (plus chromium) for the matrix; see below |
| `.github/workflows/web-e2e.yml` / docs | `playwright install --with-deps chromium` | a cross-engine job installs `firefox webkit` too; the Playwright-browser cache key (already `hashFiles('uv.lock')`) is unchanged |

`record` drives whichever engine is selected: it is AI authoring against the same `Driver`
interface, so passing `eff.browser` through `launch_driver` is the whole change — the recorded YAML
stays engine-neutral. The selector mapping (`_ROLE_MAP`), the `query()` DOM walk over `QUERY_JS`,
the resolve-through-the-core actuation, the health signals (`pageerror` / main-frame status /
`dialog`), and `capabilities()` are all engine-independent and unchanged; `QUERY_JS` is standard
DOM and `getBoundingClientRect` geometry that runs identically on all three engines.

**On-demand install.** `ensure_web_runtime(backends)` today is a no-op unless a web backend is
requested *and* the Playwright package is missing: it early-returns when web isn't requested or when
`_playwright_available()` (an import probe) is already true, and only on the missing path does it
`uv pip install playwright` then `playwright install chromium`. That package probe doesn't
distinguish *which browser binaries* are present, so installing firefox/webkit needs a per-engine
check rather than the single package probe. The design: after the package is
ensured, run `playwright install <engines>` for the engines this run needs (the resolved
`eff.browser`, or the `--browsers` list). `playwright install` is **idempotent** — the web-e2e
workflow comment already relies on this ("a stale browser is simply re-downloaded") — so it is safe
to call unconditionally for the requested engines; a missing binary is fetched and a present one is
a fast no-op. This keeps the auto-provisioning contract (`make serve` adds idb, a web run adds
Playwright) intact while widening it to the chosen engine.

**`doctor` reporting.** `doctor` already gates runnability via `preflight.runnability(actuator, …)`
and prints a fixable checklist. For the web actuator it should report **which engines are installed**
(a per-engine presence check, e.g. probing `playwright install --dry-run` output or the browser
registry path), so "you asked for `webkit` but only `chromium` is installed" surfaces here with a
one-line fix rather than as a confusing downstream launch failure — the same role the idb-version
check plays for iOS.

**Implementation status.** Phase 1 (engine selection) has shipped — the `browser` config field and
its load-time validation, the `--browser` flag on `run` / `record` with flag > config > default
precedence, the engine threaded through `PlaywrightDriver` / `make_driver` / the web environment /
`doctor`, and the on-demand `playwright install <engine>` in `ensure_web_runtime`, all covered by the
fast `make check` gate with a fake starter (no real browser). On-device firefox / webkit launch is
left to the web-e2e path. **Phase 2 (the `--browsers` matrix) has shipped** — the `--browsers` flag
on `run`, the run-per-engine fan-out (`run_matrix_and_report`) writing evidence under
`run_dir/<engine>/<sid>`, the engine-tagged `RunResult.engine`, the manifest `matrix` block and
all-must-pass `ok`, the engine-keyed JUnit `classname="bajutsu.<engine>"`, and the `report.html`
engine × scenario grid, all covered by the fast gate with fake leases. On-device cross-engine runs
remain the web-e2e path.

### Phase 2 — cross-browser matrix

**The fan-out.** A `--browsers <list>` option on `run` (e.g. `--browsers chromium,firefox,webkit`)
runs each selected scenario once per listed engine. `--browsers chromium` is exactly
`--browser chromium`; the two options are the single-engine and multi-engine spellings of one axis,
so a single-engine run never pays for the matrix machinery.

**How the fan-out maps onto the run pipeline — sequential per engine.** The run is structured as a
**loop over engines, each engine a full `run_and_report`-shaped pass**, rather than mixing engines
inside one device pool. The reason is concrete: `device_pool` selects exactly one actuator
(`select_actuator(backends)`) and builds one kind of lane, and `_resolve_lanes` already turns
`--workers N` into N near-free `web-{i}` `BrowserContext` lanes *for one engine*. Reusing
[BE-0054](../../implemented/BE-0054-web-backend-completion/BE-0054-web-backend-completion.md)'s
parallel lanes **within** an engine (so `--workers` still parallelizes scenarios) while iterating
engines **around** that pool keeps each engine's pool homogeneous and avoids threading a per-lane
engine through `device_pool` / `launch_driver` / the collector wiring. Each engine pass leases its
own pool, runs the selected scenarios via the existing `run_all`, and produces a per-engine result
list and evidence tree; the matrix is the assembly of those passes. (A future optimization could run
engines concurrently — they are independent processes — but sequential-per-engine is the v1 because
it reuses the existing single-engine path unchanged and keeps evidence directories trivially
non-colliding.)

**Evidence layout — no collisions.** Today `run_all` writes each scenario's artifacts under
`run_dir/<sid>` where `sid = f"{i:02d}-{scenario_slug(s.name)}"`. The matrix prefixes that with the
engine: `run_dir/<engine>/<sid>` (e.g. `chromium/00-login`, `webkit/00-login`), so the same scenario
on two engines never overwrites the other's `network.json`, screenshots, or video — each engine pass
is handed its own `run_dir` subtree.

**The deliverable — an engine × scenario matrix in the manifest.** `manifest.json` is the run's
single source of truth (`manifest_dict` → `"scenarios": [asdict(r) for r in results]`,
`"ok": all(r.ok …)`). The matrix extends this without breaking the v1 shape: each `RunResult`
already carries a `backend` field, so the natural representation is **one flat list of results tagged
by engine** plus an aggregate matrix view. Concretely:

- Each per-engine `RunResult` records its engine. `RunResult.backend` is `"playwright"` for every
  web result today, so rather than overload it, add an explicit `engine: str = ""` field (empty for
  iOS / single-engine) populated per pass — keeping `backend` meaning the actuator and `engine`
  meaning the rendering engine.
- The manifest gains a top-level `"matrix"` block: `{ engines: ["chromium","firefox","webkit"],
  scenarios: [<name>…], cells: { "<scenario>": { "<engine>": {ok, sid, failure} } } }` — a pure
  aggregation of the per-engine verdicts already in `scenarios`. `report.html` renders this as the
  engine × scenario grid where a row green on Chromium and Firefox but red on WebKit is the
  machine-detected incompatibility this item exists to find.
- JUnit (`junit_xml`) today emits one `<testcase classname="bajutsu">` per scenario. The matrix keys
  the engine into the case: `classname="bajutsu.<engine>"` (or a per-engine `<testsuite>`), so CI
  sees `chromium.login` and `webkit.login` as distinct cases and a per-engine failure is attributable
  in the CI UI without reading the manifest.

**Verdict semantics — machine-only, all-must-pass.** A `--browsers` run is green only if **every**
requested engine passes every selected scenario (`manifest["ok"] = all(r.ok for r in every engine's
results)`); any engine-specific failure fails the run. Each cell's `ok` is the existing deterministic
`run` verdict — machine assertions only, condition-waited, no fixed sleep, **no LLM** — and the
matrix is pure aggregation of those booleans, so prime directive #1 holds by construction: nothing in
the gate consults a model. (Whether an engine can be marked "advisory / non-blocking" — to track a
known WebKit gap without failing CI — is a possible refinement, noted under Alternatives, not v1.)

### Validation plan

The work splits cleanly across the two gates the project already runs (CLAUDE.md "the gate"):

**Fast `make check` gate (no browser, Linux, the bulk of the coverage).** Everything except the
actual browser launch is browser-free and unit-testable with fakes, matching the existing web tests
that drive `parse_dom` and an injected `_Page` without Playwright:

- *Engine resolution & precedence.* Assert `resolve()` yields `Effective.browser == "chromium"` by
  default, honours `targets.<name>.browser`, and that the `--browser` override wins over config — the
  three-tier precedence, tested the same way the `headless` / `--headed` precedence is.
- *Validation.* An invalid `browser` value fails at config load (the `field_validator`) and an
  invalid `--browser` flag exits 2 — both without touching a browser.
- *Threading.* A fake `Starter` (the existing test seam — `PlaywrightDriver(starter=…)`) records the
  engine it was asked for, proving `make_driver` / `launch_driver` forward `eff.browser` end to end.
- *Matrix assembly.* Feed the manifest/JUnit builders synthetic per-engine `RunResult`s (a green
  Chromium + a red WebKit for the same scenario) and assert the `"matrix"` block, the aggregate
  `ok`, the per-engine evidence prefixes, and the engine-keyed JUnit cases — all from data, no
  browser. This is where the "red on one engine, green on another is surfaced" contract is locked in
  deterministically.

**Web-e2e path (real browsers, Linux, heavier — not in `make check`).** `web-e2e.yml` already drives
a real headless Chromium against `demos/web`. Extend it with a cross-engine job that installs
`firefox webkit`, runs a representative `demos/web` scenario under each engine, and asserts the
per-engine verdicts — plus one fixture that deliberately depends on an engine-divergent behaviour, to
prove end to end that the matrix flags a genuine engine-specific failure (red on one, green on the
others) rather than silently passing. This is gated by a path filter like today's web-e2e and is not
a required check, so it adds the real cross-engine signal without slowing the fast gate.

### Determinism, app-agnosticism, and the gate

* **No LLM, no verdict touched by a model.** Each per-engine result is the existing deterministic
  `run`; the matrix aggregates verdicts. Prime directives #1 and #2 hold by construction.
* **App-agnostic.** The engine is an execution axis (flag/config), never scenario content; the same
  YAML runs on every engine, so prime directive #3 holds.
* **Linux-testable, inside the existing gate.** All three engines run headless on Linux, so the
  cross-engine path is exercised by the same `make check` / web-e2e CI job as the current Chromium
  path — no Mac, no emulator. The one real cost is CI download/cache of the Firefox and WebKit
  browser builds for the cross-engine job (Chromium-only runs are unaffected).

### Relationship to existing items

* **Builds on [BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)** (the
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
* [BE-0041 — Web (Playwright) backend](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)
  — the backend this extends, and its "cross-browser" seam promise.
* [BE-0054 — Web backend completion](../../implemented/BE-0054-web-backend-completion/BE-0054-web-backend-completion.md)
  — the parallel-lane pool this fan-out reuses; distinct (single-engine rich capabilities) in scope.
* [BE-0021 — AI triage](../../implemented/BE-0021-ai-triage/BE-0021-ai-triage.md) — advisory
  investigation of an engine-specific failure.
* [BE-0029 — Visual-regression assertions](../../implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md),
  [BE-0062 — Playwright codegen](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md)
  — adjacent, out-of-scope follow-ups.
* The seams this changes: `bajutsu/drivers/playwright.py` (`_start_chromium`, the `Starter` type,
  `PlaywrightDriver.__init__`), `bajutsu/backends.py` (`make_driver`, `ensure_web_runtime`,
  `capabilities_for`), `bajutsu/runner/launch.py` (`launch_driver`), `bajutsu/runner/pool.py` (the web
  lane branch + `_resolve_lanes`' `web-{i}` lanes), `bajutsu/config.py`
  (`TargetConfig.headless` → `Effective.headless` → `resolve`, the template the `browser` field
  follows), `bajutsu/cli/commands/run.py` / `record.py` (the `--headed` override the `--browser` flag
  mirrors), `bajutsu/report/manifest.py` (`manifest_dict`, `junit_xml`), and
  `.github/workflows/web-e2e.yml`; [drivers.md](../../../docs/drivers.md),
  [multi-platform.md](../../../docs/multi-platform.md).
