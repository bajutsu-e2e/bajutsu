**English** · [日本語](ja/drivers.md)

# Driver abstraction, backends, and environment management

> One `Driver` interface, behind which sit the backends — `idb` (iOS Simulator), `playwright`
> (web browser), plus the in-memory `fake` for tests — with capability differences absorbed on the
> abstraction side. A platform-aware registry picks the actuator from the `backend` list; on iOS,
> launching the app (boot/launch) is handled by a `simctl` wrapper.
>
> Implementation: `bajutsu/drivers/` (`base.py` / `idb.py` / `playwright.py` / `fake.py`) ·
> `bajutsu/backends.py` · `bajutsu/env.py`.

Related: [selectors](selectors.md) (resolution) · [the stability ladder](concepts.md#5-the-stability-ladder) · [run-loop](run-loop.md)

---

## Driver Protocol

The common interface every backend satisfies (`base.py`, a `runtime_checkable` `Protocol`).
**Actions (tap/type/swipe/wait/query) are performed by the actuator only.**

```python
class Driver(Protocol):
    def query(self) -> list[Element]: ...           # the screen's element tree
    def tap(self, sel: Selector) -> None: ...
    def tap_point(self, p: Point) -> None: ...       # raw coordinate tap (system alerts, etc.)
    def long_press(self, sel: Selector, duration: float) -> None: ...
    def swipe(self, frm: Point, to: Point) -> None: ...
    def type_text(self, text: str) -> None: ...
    def wait_for(self, sel: Selector) -> bool: ...   # single-shot: matches the current screen?
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...          # provided capabilities (for actuator / fallback resolution)
```

> **About `wait_for`**: it is **single-shot by contract** (BE-0118) — it checks the current screen
> once and returns, never looping. The deadline poll lives in one shared helper, `base.wait_until`,
> so a caller's `timeout` means the same real seconds on every backend instead of each driver
> reimplementing its own loop. The run loop's own condition waits are done by the orchestrator
> polling `query()` directly (`_wait`, [run-loop](run-loop.md#waits-condition-waits-only)); so
> `wait_until` is used only by callers outside that loop (e.g. `golden_assert`).

### Capabilities (`Capability`)

The set of tokens returned by `capabilities()`, used for actuator selection, evidence fallback
resolution, and the **preflight capability check** (below).

| Capability | Meaning | idb | playwright | fake |
|---|---|:--:|:--:|:--:|
| `query` | element-tree query | ✅ | ✅ | ✅ |
| `elements` | element-dump evidence | ✅ | ✅ | ✅ |
| `screenshot` | screenshot | ✅ | ✅ | ✅ |
| `semanticTap` | tap directly by id/label (no coordinates) | — | ✅ | ✅ |
| `conditionWait` | native condition waiting | — | ✅ | ✅ |
| `network` | native network monitoring | — | ✅ | — |
| `multiTouch` | two-finger gestures (pinch / rotate) | — | ✅ | ✅ |

> idb actuates by **frame-center coordinates** — it exposes no semantic tap, so the run loop resolves
> a unique element via `query()` and taps its center. `pinch` / `rotate` raise `UnsupportedAction`
> (single-touch); those go through codegen → XCUITest. The `fake` driver advertises a richer
> capability set (semanticTap / conditionWait / multiTouch) purely to exercise those code paths in
> tests. The `playwright` (web) driver advertises `semanticTap` / `conditionWait` (Playwright has
> both natively), `network` — the **first backend with native network**, observing and stubbing
> traffic in-process with no app-side cooperation — and `multiTouch`, synthesizing pinch / rotate via
> the Chromium DevTools protocol's `Input.dispatchTouchEvent` (BE-0054).

### Preflight capability check (BE-0082)

A backend's capability set is static, so a scenario that needs a capability the chosen actuator
lacks is knowable before any device work. At run start — after the actuator is selected, before
the first device is leased — the runner checks each scenario against the actuator's capabilities
(`bajutsu/capability_preflight.py`) and fails an unsupported scenario immediately, with one
aggregated `UnsupportedAction`-style reason, instead of booting a device and failing partway
through (prime directive #2: fail fast and clearly). It is a pure function of (scenario, capability
set) — no device, no clock — and per-scenario: only the offending scenarios fail, the rest run.

The check gates only the **hard** requirements the capability set cleanly decides: `pinch` /
`rotate` need `multiTouch`, a `visual` assertion needs `screenshot`, and every run needs `query` +
`elements`. It deliberately does **not** gate `conditionWait` (the run loop polls for every wait,
so no backend needs the token) or `network` (idb captures traffic through the app-side collector
despite not advertising `network`, so `request` / `event` / `requestSequence` / `responseSchema`
assertions and `until: { request }` waits run on idb). `gestures.py`'s `_require_multi_touch` stays
as a defense-in-depth check at gesture time.

## idb

Headless, coordinate-based. For CI (continuous integration). With no semantic tap, the abstraction resolves
**id → frame center → coordinate tap**. Implementation: `drivers/idb.py`.

- `query()`: normalizes `idb ui describe-all --udid <udid> --json` via `parse_describe_all`
  (handles both a JSON array and newline-delimited JSON, absorbing `AXLabel`/`AXValue`/`AXUniqueId`, etc.).
- `tap(sel)`: `_resolve` to confirm uniqueness (**retries not-found, fails ambiguity fast**: a
  real-device tree can be transiently empty during transitions) → `idb ui tap` (integer
  coordinates) at the frame center.
- `screenshot`: idb's own frame capture is unreliable, so it uses **`simctl io screenshot`**.
- `swipe`: adds `--duration 0.2` to make it a real drag (an instantaneous swipe is not recognized
  as a pan by SwiftUI).

> The describe-all JSON key names follow fb-idb's output and are **validated on-device** against
> fb-idb (iPhone 17 Pro, recent iOS) via `make -C demos/showcase run-swiftui` + the `e2e.yml` CI workflow; re-check them only
> if the installed idb version changes the schema (the note atop `idb.py`). The idb client is
> `uv sync --extra idb`; `idb_companion` is `brew install facebook/fb/idb-companion`.

### Tracking the idb version (BE-0005)

idb is the only on-device backend, so a new Simulator runtime an older `idb_companion` can't
drive — or a companion upgrade that reshapes the describe-all JSON — breaks a run without any
Bajutsu change. The version idb runs against is therefore a tracked, recorded input rather than
whatever happens to be installed:

- **Pin a range in config.** `defaults.idbVersion` holds a constraint like `">=1.1.8"` or
  `">=1.1.0,<2.0.0"` (environment-level — the same pin regardless of which target a scenario
  drives). `bajutsu doctor` reports the installed `idb_companion` against it, e.g.
  `✓ idb_companion version: 1.1.8 (expected >=1.1.8)`, so a mismatch surfaces in the pre-flight
  checklist instead of as a confusing downstream failure. A malformed pin is rejected at config
  load. With no pin declared, `doctor` shows no version line.
- **Recorded in the manifest.** Every idb-backed run writes the `idb_companion` and idb client
  versions into `manifest.json` (`"idb": { "companion": …, "client": … }`), so any artifact set
  states exactly which idb produced it. This is provenance only — it never affects pass/fail, so
  the run/CI verdict stays deterministic.
- **A scheduled compatibility monitor.** `idb-monitor.yml` runs the smoke scenario through idb
  against the latest `idb_companion` on a weekly cadence (separate from the per-PR gate). Because
  the smoke run goes through `parse_describe_all` → Element normalization, a schema or behaviour
  drift fails it loudly there, on a cadence we control, rather than being discovered ad hoc.

## Playwright (web)

Headless Chromium via Playwright (Python). Runs on Linux with **no Mac and no Simulator**, so it
fits the same toolchain as `make check`. Implementation: `drivers/playwright.py` (roadmap
[BE-0041](../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)).

- `query()`: one `page.evaluate()` walks the visible / interactive / a11y-relevant DOM nodes and a
  pure parser (`parse_dom`) maps each to an `Element`. The id convention is the web equivalent of
  iOS accessibilityIdentifier: `data-testid` → `Selector.id`, ARIA `role` (or tag) → `traits`,
  accessible name / `aria-label` / text → `label`, input `value` → `value`.
- `tap(sel)`: like idb, it resolves a **unique** element through the shared
  `resolve_unique`/`find_all` against a `query()` snapshot and clicks the **frame center** by
  coordinate (`page.mouse.click`). It deliberately does **not** use Playwright's own
  `get_by_test_id().click()`, so selector semantics stay byte-identical to every other backend.
- `type_text` types via `page.keyboard` (the orchestrator taps `into` first, focusing the field);
  `screenshot` is `page.screenshot`; `wait_for` is single-shot via `find_all` (like every backend —
  the shared `base.wait_until` supplies the deadline poll).
- Lifecycle is owned by the driver: a fresh `BrowserContext` is the `erase` equivalent, `navigate()`
  (`page.goto(baseUrl)`) is the `launch`, and `close()` tears the browser down. There is no simctl
  device, so the run uses a dummy lease and no device control.
- **Multi-touch** (BE-0054): `pinch` / `rotate` are synthesized as two-finger drags via the Chromium
  DevTools protocol (`Input.dispatchTouchEvent`) — `mouse` is single-pointer, so gestures go through
  CDP, the same path a real touch takes (so the page's touch listeners fire). The element center
  anchors the two fingers; `scale` spreads/closes their gap and `radians` rotates them about it.
- **Native network** (BE-0054): Playwright sees every request the page makes, so `--network` works
  on web with no app-side cooperation. `network_collector()` hooks the page's `requestfinished`
  event into the *same* `NetworkExchange` the iOS collector produces (so `request` assertions and
  `network.json` evidence are unchanged), and a scenario's `mocks` are fulfilled in-process via
  `page.route` — a matching request gets the canned response and is recorded with `mocked: true`.
  Mock matching reuses the deterministic `request` matcher, and no model is consulted.
- **Console / page-error & video evidence** (BE-0054): the `deviceLog` capture kind streams the
  browser console and uncaught page errors to `<scenario>/device.log`, and `video` records the whole
  scenario — both Playwright-native (no simctl), the web analogues of the iOS os_log / simctl video.
  The pool enables recording only when `video` is in the scenario's `capture` (the `BrowserContext`
  is created with `record_video_dir`), and the `video` interval finalizes it into
  `<scenario>/scenario.mp4` (webm content) on close. The pool injects the driver's `web_interval`
  into the `FileSink`, so the same backend-agnostic `capture` policy carries both.

> `playwright` is imported **lazily** (only when a browser is actually started), so it never loads on
> the default CLI path (locked by `tests/serve/test_import_guard.py`). Install with
> `uv sync --extra web` + `uv run playwright install chromium`; the demo at `demos/web`
> (`make -C demos/web e2e`) drives a tiny static web app end to end.

## FakeDriver

An in-memory implementation for testing the orchestrator / runner / record without a device.
Implementation: `drivers/fake.py`.

- Holds a `screen` (a list of `Element`) and returns it from `query()`.
- `tap` / `long_press` go through `resolve_unique` like the real thing (ambiguous / not-found =
  `SelectorError`).
- A `react` callback lets you script "the screen changes in response to an action."
- `actions` records the performed actions (for assertions).

```python
def react(driver, kind, arg):
    if kind == "tap":
        driver.screen = [...]  # swap in the post-tap screen
FakeDriver(screen=[...], react=react)
```

## Backend selection and the actuator

Implementation: `bajutsu/backends.py`.

```python
PLATFORMS = {                              # a platform token expands to its actuators
    "ios":     ("xcuitest", "idb"),        #   XCUITest preferred, idb fallback (BE-0019)
    "android": ("adb",),                   #   planned
    "web":     ("playwright",),            #   implemented (BE-0041)
    "fake":    ("fake",),                  #   the in-memory test/demo driver
}
IMPLEMENTED = {"idb", "fake", "playwright", "xcuitest"}  # actuators with a driver today

def default_available(actuator) -> bool:   # implemented + backing tool present (playwright: package import; fake: always)
def resolve_actuators(backends) -> list:   # expand each token (platform or actuator) to actuators
def select_actuator(backends, available) -> str:  # first implemented + available, in order
def make_driver(actuator, udid, *, base_url=None, runner_port=None) -> Driver:  # "xcuitest"→XcuitestDriver, "idb"→IdbDriver, "playwright"→PlaywrightDriver, "fake"→FakeDriver
```

- A **backend token** is either a **platform** (`ios` / `android` / `web` / `fake`) or a concrete
  **actuator** (e.g. `idb`). `ios` lists `xcuitest` ahead of `idb` (BE-0019): `--backend ios` (or
  `backend: [ios]`) prefers XCUITest when available and falls back to `idb` when it is not (e.g.
  headless CI without the XCUITest host) — the scenario and config never change.
- `backend` is an **ordered list** (most-stable-first; [concepts](concepts.md#5-the-stability-ladder)).
  Each token is expanded to its actuators, in order; the **actuator = the first implemented and
  available** one. If none is available, `RuntimeError` (the CLI exits with code 2).
- `web` resolves to `playwright`, which **is implemented** ([multi-platform](multi-platform.md));
  `android` (→ `adb`) is **declared but not implemented yet**, so requesting it raises a clear "not
  implemented yet" rather than a generic failure. Truly unknown tokens are skipped (forward-compat:
  an older build can run a config that lists a future backend).
- The availability check `available` is injectable (swappable in tests). The default is `shutil.which`
  for PATH-backed actuators; `playwright` is gated on whether its Python package is importable, and
  `fake` is always available.
- The actuator is fixed once at the start of a run and held for the whole run (so two drivers never
  operate one device).

Actuation stays with the single actuator. Non-actuator backends in the list can serve as **read-only
evidence fallbacks** (DESIGN §9, [BE-0020](../roadmaps/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md)):
a same-platform backend whose `capabilities()` advertises a kind the actuator lacks (e.g.
`Capability.NETWORK`) is resolved as the provider for that kind, accessed only through the narrow
`EvidenceProvider` Protocol (no tap/type/swipe — a type-level guarantee). When no backend can fill a
gap, the kind is skipped with a recorded reason (`SkippedCapture`) — graceful degradation, never a
run failure. See [evidence — provider](evidence.md#artifact-provenance-provider) for provenance
details.

## Environment management (simctl)

Implementation: `bajutsu/env.py`. Command builders are pure functions (unit-tested); execution goes
through an injectable `RunFn`.

| Method | Command | Notes |
|---|---|---|
| `erase()` | `simctl erase <udid>` | clean environment |
| `boot()` | `simctl boot <udid>` | idempotent if already booted (swallows the error) |
| `launch(bundle, args, env)` | `simctl launch --terminate-running-process <udid> <bundle> <args>` | env injected via `SIMCTL_CHILD_*` |
| `terminate(bundle)` | `simctl terminate <udid> <bundle>` | ignored if not running |
| `openurl(url)` | `simctl openurl <udid> <url>` | deeplink |
| `screenshot(path)` | `simctl io <udid> screenshot <path>` | — |

> **Injecting launch env**: an env var to pass to the app is set on the parent process as
> `SIMCTL_CHILD_<NAME>`, which reaches the child (the app) as `<NAME>`. `child_env()` does this
> conversion. The showcase's launch hooks like `SHOWCASE_UITEST` use this mechanism
> ([showcase](showcase.md#launch-environment-hooks)).

The `video` / `deviceLog` interval captures also use `simctl io recordVideo` / `simctl spawn log
stream`, but those live in the evidence subsystem (`intervals.py`)
([evidence](evidence.md#interval-evidence-video--devicelog)).
