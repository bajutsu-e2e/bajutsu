**English** · [日本語](ja/drivers.md)

# Driver abstraction, backends, and environment management

> One `Driver` interface, behind which sit the [backends](glossary.md#driver-backend-actuator-platform) — `xcuitest` (iOS Simulator), `adb` (Android
> emulator), `playwright` (web browser), plus the in-memory `fake` for tests — with capability
> differences absorbed on the abstraction side. A platform-aware registry picks the actuator from
> the `backend` list; on iOS, launching the app (boot/launch) is handled by a `simctl` wrapper, and
> on Android by the twin `adb` wrapper.
>
> Implementation: `bajutsu/drivers/` (`base.py` / `xcuitest.py` / `adb.py` / `playwright.py` / `fake.py`) ·
> `bajutsu/backends.py` · `bajutsu/simctl.py` · `bajutsu/adb.py`.

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
    def swipe(self, frm: Point, to: Point) -> None: ...    # a raw pointer drag (coordinate form)
    def scroll(self, frm: Point, to: Point) -> None: ...   # a directional scroll (BE-0227)
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

| Capability | Meaning | xcuitest | adb | playwright | fake |
|---|---|:--:|:--:|:--:|:--:|
| `query` | element-tree query | ✅ | ✅ | ✅ | ✅ |
| `elements` | element-dump evidence | ✅ | ✅ | ✅ | ✅ |
| `screenshot` | screenshot | ✅ | ✅ | ✅ | ✅ |
| `semanticTap` | tap directly by id/label (no coordinates) | ✅ | — | ✅ | ✅ |
| `conditionWait` | native condition waiting | ✅ | — | ✅ | ✅ |
| `network` | native network monitoring | — | — | ✅ | — |
| `multiTouch` | two-finger gestures (pinch / rotate) | ✅ | ✅ | ✅ | ✅ |
| `textSelection` | select-all + clipboard copy on the focused field | ✅ | ✅ | ✅ | ✅ |
| `deviceControl.setLocation` | set the simulated GPS location | ✅ | ✅ | — | — |
| `deviceControl.clipboard` | read / write / clear the clipboard | ✅ | ✅ | — | — |
| `deviceControl.push` | deliver a push notification | ✅ | — | — | — |
| `deviceControl.clearKeychain` | clear the keychain | ✅ | — | — | — |
| `deviceControl.appLifecycle` | background / foreground the app | ✅ | — | — | — |
| `deviceControl.statusBar` | override / clear the status bar | ✅ | — | — | — |

> The `deviceControl.*` tokens are the `DeviceControl` family split per operation (BE-0212, from the
> coarse `deviceControl` of BE-0128), so a backend can advertise exactly the operations it can
> honor. XCUITest backs the whole family through `simctl`; the Android emulator backs
> `setLocation` + `clipboard` only (its `push` / keychain / status-bar / app-lifecycle operations have
> no faithful equivalent), which the split makes expressible without green-lighting the rest.

> adb sits at the **lean end**, actuating by **frame-center coordinates** — it exposes no semantic
> tap, so the run loop resolves a unique element via `query()` and taps its center. XCUITest, by
> contrast, sits at the rich end: it taps directly by identifier, waits on native conditions, and
> performs `pinch` / `rotate` natively. adb advertises `query` / `elements` / `screenshot`,
> `multiTouch` (a rooted-device `sendevent` two-finger sweep; BE-0232), plus the emulator-backed
> device-control subset `deviceControl.setLocation` + `deviceControl.clipboard` (BE-0211); the rest
> of the device-control family has no faithful emulator equivalent and stays unadvertised. The
> `fake` driver advertises a richer capability set (semanticTap / conditionWait / multiTouch) purely
> to exercise those code paths in tests. The `playwright` (web) driver advertises `semanticTap` /
> `conditionWait` (Playwright has both natively), `network` — the **first backend with native
> network**, observing and stubbing traffic in-process with no app-side cooperation — and
> `multiTouch`, synthesizing pinch / rotate via the Chromium DevTools protocol's
> `Input.dispatchTouchEvent` (BE-0054).

### Preflight capability check (BE-0082)

A backend's capability set is static, so a scenario that needs a capability the chosen actuator
lacks is knowable before any device work. At run start — after the actuator is selected, before
the first device is leased — the runner checks each scenario against the actuator's capabilities
(`bajutsu/capability_preflight.py`) and fails an unsupported scenario immediately, with one
aggregated `UnsupportedAction`-style reason, instead of booting a device and failing partway
through (prime directive #2: fail fast and clearly). It is a pure function of (scenario, capability
set) — no device, no clock — and per-scenario: only the offending scenarios fail, the rest run.

The check gates only the **hard** requirements the capability set cleanly decides: `pinch` /
`rotate` need `multiTouch`, `select` / `copy` need `textSelection` (select-all + clipboard copy;
the web context is coordinate-only for these and refuses both — `delete` / `clear` stay ungated, as
every backend backs `delete_text`), a `visual` assertion needs `screenshot`, and each device-control step
needs the token for its own operation — `setLocation` needs `deviceControl.setLocation`, the
clipboard steps need `deviceControl.clipboard`, `push` needs `deviceControl.push`, and so on
(BE-0212 split the coarse `deviceControl` family of BE-0128 into these per-operation tokens). Every
run needs `query` + `elements`. It deliberately does **not** gate `conditionWait` (the run loop
polls for every wait, so no backend needs the token) or `network` (XCUITest captures traffic through
the app-side collector despite not advertising `network`, so `request` / `event` / `requestSequence` /
`responseSchema` assertions and `until: { request }` waits run on iOS). `gestures.py`'s
`_require_multi_touch` stays as a defense-in-depth check at gesture time, and `_need_control` stays
as the equivalent for device-control steps — catching the case where the specific run has no
`DeviceControl` wired at all, e.g. a parallel run with no pinned device. Because the tokens are
per-operation, a backend that supports only part of the family (the Android emulator: `setLocation`
+ `clipboard`) passes preflight for what it advertises and fails fast for the rest, each unsupported
step named individually — rather than the family being all-or-nothing.

## XCUITest (iOS)

The **sole iOS backend** since [BE-0290](../roadmaps/BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)
retired idb. It reads the **XCTest automation snapshot** through a **resident on-device runner**
(`BajutsuKit`) driven over a loopback HTTP channel, and drives an arbitrary app by bundle id with no
app-side integration. Implementation: `drivers/xcuitest.py`. It sits at the rich end of the
capability model — semantic tap, native condition waiting, multi-touch, and text selection — rather
than resolving through frame-center coordinates. Needs Xcode's `xcodebuild`.

- `query()`: reads the XCTest automation snapshot and maps each element to an `Element`. The
  snapshot **descends into group containers**, so — unlike a coordinate backend's flat frame dump —
  it renders a **fully-expanded element tree** (`AXLabel`/`AXValue`/accessibility identifier
  mapped to `label`/`value`/`id`).
- `tap(sel)`: `_resolve` confirms uniqueness (**retries not-found, fails ambiguity fast**: a
  real-device tree can be transiently empty during transitions), then taps the element **directly by
  its accessibility identifier** — a semantic tap, no coordinates (BE-0289 re-resolves a stale
  snapshot handle and re-actuates only on a still-unique match).
- `wait_for`: uses the runner's native condition waiting.
- `pinch` / `rotate`: two-finger multi-touch gestures performed natively by the runner.
- `select` / `copy`: native text selection on the focused field.
- `screenshot`: `simctl io screenshot`.

> The generic runner uses `XCUIApplication(bundleIdentifier:)`, so it drives any installed app with
> no app-side cooperation. A Simulator run needs no runner config at all: when a target names neither
> `xcuitest.testRunner` nor `xcuitest.build`, it resolves to the Simulator runner bundled in the wheel
> as package data (BE-0292) — an explicit `testRunner` or `build` still overrides that default, and
> `deviceType: device` still requires an explicit signed runner, since Bajutsu cannot ship one signed
> for the operator's team. The backend is **validated on-device** (iPhone 17 Pro, recent iOS) via
> `make -C demos/showcase run-swiftui` + the `ios-e2e.yml` CI workflow. The XCUITest backend needs no
> pip extra — Xcode supplies `xcodebuild`.

## adb (Android)

Headless, coordinate-based — the only coordinate backend. With no semantic tap, the
abstraction resolves **id → frame center → coordinate tap**. Implementation:
`drivers/adb.py` + `bajutsu/adb.py` (roadmap
[BE-0007](../roadmaps/BE-0007-android-backend/BE-0007-android-backend.md)).

- `query()`: reads the window's UI Automator XML and maps each `<node>` to an `Element` with a pure
  parser (`parse_hierarchy`). The read runs over the **resident UI Automator server** when it is
  built (`make -C BajutsuAndroidUIAutomatorServer build`) — one warm `UiAutomation` session answering
  `GET /source` over `adb forward`, so each read costs ≈ 0.1–0.3 s instead of the ≈ 2.4 s a fresh
  `adb -s <serial> exec-out uiautomator dump /dev/tty` pays per invocation (roadmap
  [BE-0245](../roadmaps/BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md));
  the resident whole-screen dump is narrowed to the active window so it yields the same Elements.
  Without the built server — or on any channel failure — it falls back to `uiautomator dump`, and
  `BAJUTSU_ADB_RESIDENT` (`0`/`1`) pins either path. The selector mapping is
  `resource-id` → `identifier` (the `<package>:id/` prefix stripped to the local name, so a Compose
  `testTag` surfaced via `testTagsAsResourceId` reproduces verbatim while a native `android:id`
  drops its prefix), `text` → `label` (`content-desc` fallback), `content-desc` → `value` (the app
  mirrors its state value there, SPEC §2.1), and the widget `class` (plus enabled / selected /
  checked state) → `traits`. The local name is matched **exactly** — the driver does no `.`↔`_`
  rewriting, which would conflate distinct ids and erode determinism. Where a platform's native id
  syntax cannot reproduce the SPEC id verbatim (Android Views: `android:id` allows neither `.` nor
  `-`, so `stable.refresh` surfaces as `stable_refresh`), the scenario carries **both** id forms in
  one selector — `id: [stable.refresh, stable_refresh]` — and the match is an OR over the candidates
  (BE-0221); see [scenarios](scenarios.md#selectors-addressing-an-element).
- `tap(sel)`: `_resolve` confirms uniqueness (**retries not-found, fails ambiguity fast** — a
  mid-transition dump is a transient null-root that is retried, and a 2+ match fails
  immediately) → `adb shell input tap` at the frame center. `swipe` adds a finite duration so it is a
  real drag; `long_press` is a same-point swipe held for the duration; `type_text` is `input text`
  (spaces sent as its `%s` escape).
- **On-device actuation fidelity** (roadmap
  [BE-0210](../roadmaps/BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md)):
  the `back` step is the true system back (`input keyevent 4` / `KEYCODE_BACK`) — Android has no
  on-screen back element to tap, unlike iOS's OS back button; `double_tap` issues both taps in **one
  `adb shell` round-trip** (`input tap … ; input tap …`) so the adb transport round-trip does not
  widen the gap past the platform's double-tap window; and a tap whose target is **not in the current
  viewport** scrolls toward it (a default up-swipe) and re-queries, bounded by a retry count — a
  condition wait, so a selector that never appears still fails deterministically.

  > [!NOTE]
  > Scroll-into-view is an **adb-only** recovery today: XCUITest / Playwright still fail a
  > `tap` fast when the target is not in the initial viewport. So a `tap` on a below-the-fold element
  > can pass on Android (after up to a few swipes) yet fail on iOS/web for the same scenario. The
  > portable idiom stays an **explicit `swipe` step** (see `demos/showcase/scenarios/notices.yaml`);
  > the adb auto-scroll is a robustness net, not a substitute for it. Widening it to the other
  > backends is a follow-up (BE-0210 scoped it to adb).
- **Multi-touch** (BE-0232): `pinch` / `rotate` drive a two-slot protocol-B `sendevent` sweep
  (`pinch_contacts` / `rotate_contacts` compute the two contacts' geometry; `rotate` sweeps the
  straight chord between the endpoints, a linear approximation of the arc, like the web backend's
  rotate). This needs a rooted device with a discoverable touchscreen; `_two_finger_gesture` fails
  loudly with `UnsupportedAction` otherwise — there is no single-touch fallback, unlike the
  double-tap path below. `MULTI_TOUCH` is declared statically in the capability set regardless of
  root, so preflight admits `gestures_multitouch` on adb; the root check is enforced at actuation
  time, not in the capability set.
- `screenshot` writes the PNG bytes from `adb exec-out screencap -p` (binary-clean stdout).
- Lifecycle (`AndroidEnvironment`, the twin of the iOS `simctl` sequence): boot-readiness wait
  (polling `getprop sys.boot_completed` to a bounded deadline — a condition wait, no fixed sleep, and
  no unbounded `adb wait-for-device` block) →
  optional APK install → `pm clear` for a clean state (the `erase` equivalent) → `am force-stop` →
  runtime-permission pre-grant (`pm grant`, see below) → `am start` (the launcher activity resolved
  via the package manager; launch env forwarded as intent extras) → deeplink
  (`am start -a android.intent.action.VIEW`). The run manifest records `backend: "adb"` so the
  selected actuator is disclosed.
- **Runtime permissions** (BE-0210): the permissions listed in the target's config
  `grantPermissions` are granted up front with `adb shell pm grant <package> <permission>` at lease
  time — after `pm clear` (which resets grants) and before launch — so a runtime permission prompt
  never blocks a scenario. Granting deterministically up front, rather than tapping the dialog when
  it appears, keeps timing off the run path; the list is app-specific, so it lives in config, not the
  driver.
- **Interval evidence** (BE-0007 Unit 4): `video` records via `adb shell screenrecord` and
  `deviceLog` streams `adb logcat`, the twins of the simctl providers. `screenrecord` writes
  device-side (it cannot stream to a host file), so the recording is finalized on SIGINT and pulled
  off with `adb pull` on stop; `logcat` streams to the file and stops on SIGTERM. Both are supplied
  through the same driver `driver_interval` seam the web backend uses, so the backend-independent
  `capture` policy drives them unchanged (see [evidence](evidence.md)).
- **Network** is not observed natively (no `NETWORK` capability) — the same mocked story as iOS: the
  app-side collector URL is forwarded through the launch env as an intent extra, so `mocks` work with
  no new code path. Device control backs the emulator subset `setLocation` (`emu geo fix`, BE-0211)
  and the clipboard operations; the rest of the family stays unsupported. The clipboard runs through
  an in-app receiver (`BajutsuAndroid`, BE-0233), not `cmd clipboard`: on-device that command is a
  silent no-op, and since Android 10 only the foreground app / default IME may touch the clipboard —
  so bajutsu sends an ordered `am broadcast` that a receiver inside the app handles from the app
  process (base64 both ways, so the argv needs no quoting; a missing receiver fails loudly rather
  than reading an empty clip). adb still advertises `clipboard` because, like XCUITest's over simctl, the
  backend can drive it given a cooperating app. See [`BajutsuAndroid`](../BajutsuAndroid/README.md).

> The XML attribute names follow UI Automator's `uiautomator dump` schema. The Views `android:id`
> `.`↔`_` case is resolved scenario-side: a selector lists both id forms and matches either (BE-0221),
> so the shared showcase scenarios run unchanged on both Android toolkits — checked on every push/PR
> by [`android-e2e.yml`](../.github/workflows/android-e2e.yml), which drives `showcase-compose` and
> `showcase-views` over the same set. The fast gate exercises the parser, the frame-center taps, the
> transient-empty retry, and ambiguous-fails-fast over captured XML fixtures. adb is
> `brew install android-platform-tools`.

## Playwright (web)

Headless Chromium via Playwright (Python). Runs on Linux with **no Mac and no Simulator**, so it
fits the same toolchain as `make check`. Implementation: `drivers/playwright.py` (roadmap
[BE-0041](../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)).

- `query()`: one `page.evaluate()` walks the visible / interactive / a11y-relevant DOM nodes and a
  pure parser (`parse_dom`) maps each to an `Element`. The id convention is the web equivalent of
  iOS accessibilityIdentifier: `data-testid` → `Selector.id`, ARIA `role` (or tag) → `traits`,
  accessible name / `aria-label` / text → `label`, input `value` → `value`.
- `tap(sel)`: like the adb backend, it resolves a **unique** element through the shared
  `resolve_unique`/`find_all` against a `query()` snapshot and clicks the **frame center** by
  coordinate (`page.mouse.click`). It deliberately does **not** use Playwright's own
  `get_by_test_id().click()`, so selector semantics stay byte-identical to every other backend.
- `type_text` types via `page.keyboard` (the orchestrator taps `into` first, focusing the field);
  `screenshot` is `page.screenshot`; `wait_for` is single-shot via `find_all` (like every backend —
  the shared `base.wait_until` supplies the deadline poll).
- Lifecycle is owned by the driver: a fresh `BrowserContext` is the `erase` equivalent, `navigate()`
  (`page.goto(baseUrl)`) is the `launch`, and `close()` tears the browser down. There is no simctl
  device, so the run uses a dummy lease and no device control.
- **Device mode** (BE-0228): a web target's `deviceMode` config selects how each `BrowserContext` is
  created — `desktop` (the default, a plain desktop context, unchanged from before) or a Playwright
  device preset name (e.g. `iPhone 13`). A preset is resolved against `playwright.devices` and its
  descriptor (viewport / `device_scale_factor` / `is_mobile` / `has_touch` / `user_agent`) is merged
  into `new_context(**kwargs)` alongside `reduced_motion="reduce"`, so the target is driven as that
  mobile device. The descriptor is resolved **lazily** (config load never imports Playwright) and
  memoized, so a `reset_context` (crawl clean start) and a `relaunch` (BE-0077) rebuild the identical
  context — the mode is stable across the browser's whole lifecycle, the same invariant the engine
  and `reduced_motion` already hold. An unknown preset fails loudly with a `ValueError` at driver
  start. Device mode is **desktop-browser emulation** — a mobile viewport and touch input in a desktop-class
  browser, exactly what Chrome DevTools' device toolbar does — **not** a real mobile browser on a
  real device or a device cloud; for a real mobile OS the Android backend is the path.
- **Directional `swipe` scrolls** (BE-0227): the directional form `swipe: { on, direction }` means
  "scroll", and a mouse drag does not scroll a web page, so the web backend dispatches the input
  primitive that actually scrolls, keyed on the context's input mode (the `deviceMode` above). On a
  **desktop** (pointer) context it emits a `page.mouse.wheel(...)` over the gesture's start — the
  wheel is the reverse of the travel, so an `up` swipe scrolls the page **down**, exactly as a
  trackpad or wheel would. On a **touch** context (a mobile `deviceMode`) it uses a real
  single-finger touch drag over CDP (the same path `pinch` / `rotate` take), so the page's touch and
  scroll listeners fire. The **coordinate** form `swipe: { from, to }` is unchanged — it stays a
  literal `page.mouse` drag, the raw-drag last resort for a canvas / map pan / drag handle. `codegen`
  emits the desktop wheel scroll for the directional form, so a generated Playwright test scrolls in
  the physically correct direction instead of the old inert drag (a fixed default distance, as codegen
  has no viewport to scale `amount` against). The separate `drag` action (element-anchored pointer
  drag — a resize divider, a slider) routes to the driver's `swipe`, so on web it is a real
  `page.mouse` drag that *moves* the grabbed element, where a directional `swipe` would only scroll.
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
  `<scenario>/scenario.mp4` (webm content) on close. The pool injects the driver's `driver_interval`
  (the driver-supplied interval seam, shared with the adb backend) into the `FileSink`, so the same
  backend-agnostic `capture` policy carries both.

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
PLATFORMS = {                              # a platform token expands to its actuators (stability order)
    "ios":     ("xcuitest",),              #   the sole iOS actuator since BE-0290 retired idb
    "android": ("adb",),                   #   planned
    "web":     ("playwright",),            #   implemented (BE-0041)
    "fake":    ("fake",),                  #   the in-memory test/demo driver
}
COST_ORDER: dict[str, tuple[str, ...]] = {}  # empty: no platform's cost order differs from its stability order
IMPLEMENTED = {"fake", "playwright", "xcuitest"}  # actuators with a driver today

def default_available(actuator) -> bool:   # implemented + backing tool present (playwright: package import; fake: always)
def resolve_actuators(backends) -> list:   # expand each token (platform or actuator) to actuators
def select_actuator(backends, available) -> str:  # first implemented + available, in stability order
def select_actuator_cost_first(backends, available) -> str:  # cheapest available, no scenario in hand (BE-0267)
def select_actuator_for_scenario(backends, scenario, available, caps) -> str:  # cheapest available + sufficient (BE-0240)
def make_driver(actuator, udid, *, base_url=None, runner_port=None) -> Driver:  # "xcuitest"→XcuitestDriver, "playwright"→PlaywrightDriver, "fake"→FakeDriver
```

- A **backend token** is either a **platform** (`ios` / `android` / `web` / `fake`) or a concrete
  **actuator** (e.g. `xcuitest`). Each platform today resolves to a single actuator — `ios` to
  `xcuitest` (BE-0290 retired idb, so `--backend ios` and `--backend xcuitest` are equivalent),
  `android` to `adb`, `web` to `playwright`. The machinery for a **multi-actuator** platform
  (per-scenario resolution in cost order; BE-0240) stays in place for a future platform, but no
  platform exercises it today.
- Two orderings answer two questions. **Stability order** (`PLATFORMS`, most-capable-first;
  [concepts](concepts.md#5-the-stability-ladder)) drives `select_actuator` — the availability-only
  pick used where no scenario is in hand yet and cost doesn't matter (`doctor`, the pool's up-front
  setup, an explicit single-actuator pin). **Cost order** (`COST_ORDER`, cheapest-first) drives both
  `select_actuator_for_scenario` and `select_actuator_cost_first`, which share a candidate-resolution
  prefix (`_cost_ordered_available`); with `COST_ORDER` now empty, a platform's cost order is just
  its stability order, so these fall through to a single candidate. `select_actuator_for_scenario`
  additionally reuses `capability_preflight.unsupported` (BE-0082) against each candidate's capability
  set and returns the first that is both available and sufficient for that scenario's steps.
  `select_actuator_cost_first` is the same cost-first pick with no scenario to check against — used
  where a live session needs the cheapest actuator it can bring up without capability escalation
  (serve's Author-tab **Capture** and **Enrich**; BE-0267). Both delegate to `select_actuator`
  (keeping its diagnostics) whenever the resolved candidates collapse to one — which, with every
  platform single-actuator today, is always the case. If none is available, `RuntimeError` (the CLI
  exits with code 2).
- `web` resolves to `playwright` and `android` resolves to `adb`, both **implemented**
  ([vision → reach](vision.md#1-reach--more-platforms-and-surfaces)). Truly unknown tokens are
  skipped (forward-compat: an older build can run a config that lists a future backend).
- The availability check `available` is injectable (swappable in tests). The default is `shutil.which`
  for PATH-backed actuators; `playwright` is gated on whether its Python package is importable, and
  `fake` is always available.
- The actuator is fixed **per scenario** and held for that scenario's whole execution (BE-0240), so
  two drivers never operate one device at once. Fixing the actuator per scenario narrows the earlier "fixed per invocation" unit
  without relaxing the single-actuator rule: at every instant exactly one actuator acts on the leased
  device, and there is never a mid-scenario driver swap.

Actuation stays with the single actuator. Non-actuator backends in the list can serve as **read-only
evidence fallbacks** (DESIGN §9, [BE-0020](../roadmaps/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md)):
a same-platform backend whose `capabilities()` advertises a kind the actuator lacks (e.g.
`Capability.NETWORK`) is resolved as the provider for that kind, accessed only through the narrow
`EvidenceProvider` Protocol (no tap/type/swipe — a type-level guarantee). When no backend can fill a
gap, the kind is skipped with a recorded reason (`SkippedCapture`) — graceful degradation, never a
run failure. See [evidence — provider](evidence.md#artifact-provenance-provider) for provenance
details.

## Environment management (simctl)

Implementation: `bajutsu/simctl.py`. Command builders are pure functions (unit-tested); execution goes
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
stream`, but those live in the evidence subsystem (`evidence/intervals.py`)
([evidence](evidence.md#interval-evidence-video--devicelog)).
