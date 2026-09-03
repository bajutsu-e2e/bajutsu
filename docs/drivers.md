**English** · [日本語](ja/drivers.md)

# Driver abstraction, backends, and environment management

> One `Driver` interface, behind which sit the [backends](glossary.md#driver-backend-actuator-platform) — `xcuitest` (iOS Simulator), `adb` (Android
> emulator), `playwright` (web browser), plus the in-memory `fake` for tests — with capability
> differences absorbed on the abstraction side. A platform-aware registry picks the actuator from
> the `backend` list; on iOS, launching the app (boot/launch) is handled by a `simctl` wrapper, and
> on Android by the twin `adb` wrapper.
>
> Implementation: `bajutsu/common/drivers/` (`base.py` / `xcuitest.py` / `adb.py` / `playwright.py` / `fake.py`) ·
> `bajutsu/common/backends.py` · `bajutsu/common/backend_cli/simctl.py` · `bajutsu/common/backend_cli/adb.py`.

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
    def scroll(self, frm: Point, to: Point) -> None: ...   # a non-inertial directional scroll (BE-0227, BE-0326)
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
| `selectOption` | set a native `<select>` by value (web only) | — | — | ✅ | ✅ |
| `handleSystemAlert` | tap an iOS SpringBoard permission-prompt button natively | ✅ | — | — | ✅ |
| `pickerWheel` | set a wheel-style picker to a named row (iOS only) | ✅ | — | — | ✅ |
| `handleTipkitTip` | dismiss a blocking Apple TipKit tip (iOS only) | ✅ | — | — | ✅ |
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
(`bajutsu/common/capability/capability_preflight.py`) and fails an unsupported scenario immediately, with one
aggregated `UnsupportedAction`-style reason, instead of booting a device and failing partway
through (prime directive #2: fail fast and clearly). It is a pure function of (scenario, capability
set) — no device, no clock — and per-scenario: only the offending scenarios fail, the rest run.

The check gates only the **hard** requirements the capability set cleanly decides: `pinch` /
`rotate` need `multiTouch`, `selectOption` needs the `selectOption` token (a web-only `<select>`
switch; iOS / Android are rejected before any device work), `select` / `copy` need `textSelection`
(select-all + clipboard copy; the web context is coordinate-only for these and refuses both —
`delete` / `clear` stay ungated, as every backend backs `delete_text`), a `visual` assertion needs
`screenshot`, `handleSystemAlert` needs the `handleSystemAlert` token (only xcuitest declares it),
`setPickerValue` needs the `pickerWheel` token (also xcuitest only — a picker wheel is a native iOS
control, so Android and web are rejected before any device work),
and each device-control step needs the token for its own operation — `setLocation` needs
`deviceControl.setLocation`, the clipboard steps need `deviceControl.clipboard`, `push` needs
`deviceControl.push`, and so on (BE-0212 split the coarse `deviceControl` family of BE-0128 into
these per-operation tokens); a `permissions` entry is likewise gated per service
(`deviceControl.permissions.<service>`), so an unsupported service is named individually rather than
the field as a whole. Every run needs `query` + `elements`. It deliberately does **not** gate `conditionWait` (the run loop
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
app-side integration. Implementation: `common/drivers/xcuitest.py`. It sits at the rich end of the
capability model — semantic tap, native condition waiting, multi-touch, and text selection — rather
than resolving through frame-center coordinates. Needs Xcode's `xcodebuild`.

- `query()`: reads the XCTest automation snapshot and maps each element to an `Element`. The
  snapshot **descends into group containers**, so — unlike a coordinate backend's flat frame dump —
  it renders a **fully-expanded element tree** (`AXLabel`/`AXValue`/accessibility identifier
  mapped to `label`/`value`/`id`).
- `query()` also reads a presented **`SFSafariViewController`** — the in-app browser an app opens for
  a sign-in page or a terms document — from `com.apple.SafariViewService`, the process that draws it,
  the same way BE-0316 reads a SpringBoard prompt
  ([BE-0396](../roadmaps/BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)).
  The app's own snapshot reports that browser differently per iOS version — through iOS 18 it mirrors
  the whole subtree, from iOS 26 it stops at the process boundary and reports nothing below it — so
  the mirror is **pruned** and the service's own tree merged in its place, leaving one tree that is
  complete on both and reports nothing twice. The **dismiss control** is the one chrome identity the
  versions disagree on (iOS 26 identifies it `Close`, iOS 18 leaves it unidentified with the label
  `Done`), so the runner reports iOS 26's `Close` as the **identifier** on both and `id: Close`
  addresses it with one selector. Only the identifier is normalized — the label stays what the
  platform announces (`Done` on iOS 18), so a `label` selector still does not travel.
  A browser element **actuates at its frame centre**: `XCUIElement.tap()` reaches the page
  content across the process boundary but is silently dropped by the browser's own chrome. iOS 18's
  disabled `ForwardButton` has no iOS 26 counterpart at all, so a scenario cannot depend on it.
- `tap(sel)`: `_resolve` confirms uniqueness (**retries not-found, fails ambiguity fast**: a
  real-device tree can be transiently empty during transitions), then taps the element **directly by
  its accessibility identifier** — a semantic tap, no coordinates (BE-0289 re-resolves a stale
  snapshot handle and re-actuates only on a still-unique match). A tap XCTest *refuses* takes one
  more step: iOS can report a container inflated over the control it wraps, so the driver probes the
  target's named descendants and, where **exactly one** is reachable, taps that one and records
  `substitution: soleHittableDescendant`. Where none or several are, it fails and names the
  candidates rather than choosing between them
  ([selectors](selectors.md#elementnottappable-a-resolved-but-unreachable-target)).
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
`common/drivers/adb.py` + `bajutsu/common/backend_cli/adb.py` (roadmap
[BE-0007](../roadmaps/BE-0007-android-backend/BE-0007-android-backend.md)).

### Reading the tree and resolving a selector

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
  mid-transition dump is a transient null-root that is retried, and a 2+ match fails immediately).
  `tap`, `long_press`, and `double_tap` then send the resolved element's identity — its raw
  accessibility fields plus an ordinal, never a host-computed coordinate — to the resident server's `POST /act`
  (roadmap [BE-0339](../roadmaps/BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation.md)):
  the server re-resolves that identity against its own live tree and injects from the same warm
  session, so a gesture lands on the bounds the device holds at the moment it injects, never a
  coordinate the host computed one round trip earlier. A `stale` reply — the identity's match count
  moved since the host counted it — is retried, bounded; the driver then falls back to a
  host-computed coordinate (`adb shell input tap` at the frame center for `tap`, a same-point swipe
  held for the duration for `long_press`) once retries exhaust, once the channel has no `/act`
  endpoint (an older server), or once the channel faults outright. When the server injects but its
  reply never reaches the host, the driver treats the gesture as done rather than risk a second touch
  landing on top of the first. `double_tap`'s device path stamps both `MotionEvent`s from one server-side call,
  declaring a fixed interval between them instead of leaving the gap to a round trip's incidental
  timing; see **On-device actuation fidelity** below for its coordinate fallback. `swipe` adds a
  finite duration so it is a real drag; `type_text` is `input text` (spaces sent as its `%s`
  escape).

### Waiting for the tree to catch up with a pan

- **A coordinate resolve waits for the tree to catch up with a pan.** Android moves the content first
  and publishes the accessibility update naming the new frames afterwards. A read landing between those
  two moments describes the pre-scroll screen. Repeated reads then agree with each other on frames that
  are already wrong, so the two-consecutive-equal-reads settle cannot detect the lag on its own: the
  tree is *self-consistently* stale rather than visibly unsettled. After a `swipe`, a `scroll`, a
  `pinch`, or a `rotate` — every gesture that moves frames wholesale — **and after every `tap`,
  `longPress`, or `doubleTap`, device-side or coordinate alike** (BE-0332), the driver records the
  frame projection the screen had beforehand, and the next coordinate resolve re-reads until the
  projection moves off that record and then holds still briefly, bounded by a wall-clock budget it
  announces spending in full. A tap can move the layout too — open a menu, expand a row, advance a
  stepper — so the actuator that follows one must resolve against the tree the device published
  *after* it, not the pre-tap one; `tapPoint` (raw coordinates) and `back` (no resolved target) do
  not arm the wait, since they have no target-from-a-layout to postdate. The device-side `POST /act`
  path above answers the barrier's own question at its source, so a gesture the device confirms arms
  no barrier at all (BE-0339). Having injected, the resident server waits briefly on the accessibility
  event stream its warm session already observes, and reports back the device-clock time of the first
  event that postdates the injection. A gesture confirmed that way has reached the tree before the
  host is told the gesture landed, which is exactly what the barrier would otherwise have waited for.
  A gesture the device cannot confirm within that window arms the barrier exactly as a coordinate
  injection does, and three unrelated causes land there together: a gesture that moved no frame, a
  publish slower than the window, and a server old enough never to have waited at all. Confirmation
  is the device's to give and never the driver's to assume, because a coordinate-resolving follower
  (`pinch`, `rotate`, a directional `swipe`/`drag` anchor) has no `stale` re-resolve to self-heal
  with, unlike an identity-addressed follower.
  The barrier's own wall-clock budget — not the device's publish window — is the same number the
  `scroll` loop uses to confirm an
  end of content before failing (`ReadLagProvider`, BE-0326 / BE-0332; see
  [architecture](architecture.md)) — one publish lag, so one budget, spent across those paths.
  A directional `swipe` and a `drag` are the one exception to *where* the resolve happens: their
  endpoints are computed above the driver, from the anchor element the step names, so the driver
  receives two coordinates rather than a selector and cannot settle the tree itself. A backend that
  needs the settle exposes it (`SettledReadProvider`), and the handler takes that read in place of a
  bare one; a backend that does not implement the protocol keeps its single read. Without that seam,
  two consecutive directional swipes anchor the second one on the first one's pre-pan frames.
  Three conditions make that test mean "caught up" rather than merely "different". The hold matters
  because the catch-up is not atomic: Android republishes node bounds one node at a time, so a read
  landing mid-catch-up carries some new frames and some old, and two fast reads can both land inside
  that window and agree. A degenerate read is ignored outright, because its empty projection differs
  from every real one and would otherwise spend the budget on a tree the read path is still retrying.
  And the recorded projection is re-read when something has actuated since the last read, because a
  baseline predating that actuation is worse than none: the first post-gesture read moves off it, which
  would count as the gesture being published. A gesture still waiting to publish is drained before the
  next one's baseline is taken, since re-reading cannot rescue that case — the read would return the
  pre-gesture screen, and the earlier gesture's publish would later be mistaken for the newer one's.
  Every read counts toward the test, not only the ones the wait itself issues, so the reads the runner
  already takes between the gesture and the next actuator — a `wait`, an `assert`, a post-step capture
  — normally close it and a run whose tree keeps up waits for nothing.
  This wait fixes the intermittent `gestures` flake on the continuous-integration emulator, where the
  tree withheld a 73px scroll for over a second. The `longPress` aimed 10px past the target's bottom
  edge, so the mirrored value stayed `idle` even though the screenshots for those steps stayed
  pixel-identical.
  The same publish lag reaches a mid-scenario `extract` (BE-0332): a value an action mirrors into the
  tree can land a beat after the action returns, so the first reads after the action agree with each
  other on the pre-action value — `extract.yaml` bound a counter's previous value and the follow-up
  `assert` against the live one failed a correct run. There the settle poll cannot use the pan's
  "differs from the pre-step read" test, because an `extract` baseline is itself a single post-action
  read that can be stale; it instead requires the agreeing read to postdate the action by the budget.
  **Declaring a read lag is therefore a contract**: a backend that returns a non-zero `read_lag()`
  takes on that every coordinate resolve after a content-moving actuation, and every mid-scenario
  `extract`, may spend up to that budget waiting for the tree to catch up — paid only on a read that
  still matches the pre-action screen, never on one that already landed. A backend that declares none
  keeps its single-read, fail-fast behavior unchanged. The resident reader publishes a read mark the
  host compares against (BE-0332 Units 3–4), which turns that ceiling into an early-releasing wait. The
  reader observes the accessibility event stream and stamps every `GET /source` with an
  `X-Bajutsu-Read-Mark` header — the device-clock time of the newest event as of the served dump — and
  adds a `GET /clock` endpoint. The driver takes a device-clock mark before each barrier-arming
  actuation and requires a read whose mark postdates it (`read_postdates_actuation()`, the
  `ReadOrderProvider` seam), so the coordinate resolve's catch-up releases the instant the device
  publishes the action's update — no dwell — instead of idling to the budget.
  Both marks come from the device's own clock, so no host-to-device skew enters.
  The mark releases the **coordinate** barrier only. A mid-scenario `extract` keeps the wall-clock
  budget, because the mark answers "an accessibility event postdates the gesture" while `extract`
  needs "the property I am copying out has been republished". One gesture produces several events —
  Compose publishes the tapped button's own event before the `Text` mirroring the new count
  recomposes — so a read can postdate the tap, still carry the previous value, and agree with the read
  after it. Ordering is the right question for frames, which the coordinate resolve waits on, and the
  wrong one for a value. `GET
  /source?since=<mark>` pushes the same ordering into the reader itself: it blocks until an
  accessibility event postdates the requested mark, then a bounded settle closes tearing before it
  answers, so the catch-up barrier's own dwell — the two-identical-dumps freshness check it otherwise
  needs — is retired at its source and pays no second dump closing *that* barrier. The budget survives
  only for the one-shot `uiautomator dump` fallback, which carries no mark. That marked-read contract —
  a read on a read-ordering backend postdates a content-moving gesture — is checked against the real
  backend by the driver conformance suite (BE-0114).
  A mark postdate proves the read is ordered after the gesture, not that the screen has stopped moving
  since (a fling can keep publishing well past it) — so the settle poll layered on top
  (`_settle()`, above) deliberately does not treat a mark-closed catch-up alone as proof of rest
  (roadmap
  [BE-XXXX](../roadmaps/BE-XXXX-adb-settle-proven-key/BE-XXXX-adb-settle-proven-key.md)). On the
  resident channel this means a coordinate resolve still pays one confirmatory read (plus a short
  poll sleep) after the catch-up barrier itself closes for free — the barrier's own dump is free, the
  settle poll's is not.

### On-device actuation

- **On-device actuation fidelity** (roadmap
  [BE-0210](../roadmaps/BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md)):
  the `back` step is the true system back (`input keyevent 4` / `KEYCODE_BACK`) — Android has no
  on-screen back element to tap, unlike iOS's OS back button. `double_tap`'s primary path is the
  resident server's `POST /act` (see above): the server builds both `MotionEvent`s itself and stamps
  a declared interval between them, rather than trusting a round trip's incidental timing to land
  inside the platform's double-tap window (roadmap
  [BE-0339](../roadmaps/BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation.md)).
  Without that channel it falls back to a host recipe: on a rooted device with a discoverable
  touchscreen, a raw two-slot `sendevent` sequence (BE-0208) narrows the gap between the two taps to
  five process spawns; otherwise both taps go out in **one `adb shell` round trip**
  (`input tap … ; input tap …`), so the transport round trip itself does not widen the gap past the
  double-tap window. And a tap whose target is **not in the current viewport** scrolls toward it (a
  default up-swipe) and re-queries, bounded by a retry count — a condition wait, so a selector that
  never appears still fails deterministically.

  > [!NOTE]
  > This not-found scroll recovery is adb-only: XCUITest / Playwright still fail a `tap` fast when
  > the target is not in the initial viewport, so relying on it makes a `tap` on a below-the-fold
  > element pass on Android yet fail on iOS/web for the same scenario. The portable way to reach an
  > off-screen element is the explicit **[`scroll` action](scenarios.md#scroll)** (BE-0326): one
  > deterministic, non-inertial construct that reveals a target identically on iOS, Android, and
  > web — `scroll: { to: <selector> }` then act on it. It supersedes the hand-tuned `swipe` chain the
  > showcase fixture once used. The adb auto-scroll remains a robustness net under `tap` for the
  > not-found case specifically, not the portable idiom.
  >
  > A **different, narrower** safety net now covers every backend that can hit-test a point (all
  > except the app-embedded WebView bridge, `WebContextDriver`, whose protocol exposes none): before
  > acting, `tap` / `double_tap` / `long_press` check that the resolved target is actually reachable
  > at its own point — not covered by another on-screen element — using the idiomatic signal each
  > platform offers (iOS: native `isHittable`; web: a `document.elementFromPoint` hit-test; adb: a
  > document-order geometric proxy, `Driver.is_tappable` /
  > [`topmost_at_point`](../bajutsu/common/drivers/base.py)). When the check fails, the orchestrator takes
  > a small, bounded scroll — up to three `down` steps, then, only if `down` never clears it, up to
  > six `up` steps (widened, since `up` must first retrace the ground `down` already covered before
  > it can make any net progress of its own) — and retries the action once, rather than failing
  > immediately — see
  > [`selectors.md`](selectors.md#elementnottappable-a-resolved-but-unreachable-target). This is not
  > a substitute for the explicit `scroll` action above: an author who already knows a target starts
  > off-screen still writes `scroll`. It only insures against an obstruction the author did not
  > expect (a transient overlay, a sticky header settling into place), the same way adb's own
  > not-found fallback already insures against an unexpected off-screen target.
- **Multi-touch** (BE-0232): `pinch` / `rotate` drive a two-slot protocol-B `sendevent` sweep
  (`pinch_contacts` / `rotate_contacts` compute the two contacts' geometry; `rotate` sweeps the
  straight chord between the endpoints, a linear approximation of the arc, like the web backend's
  rotate). This needs a rooted device with a discoverable touchscreen; `_two_finger_gesture` fails
  loudly with `UnsupportedAction` otherwise — there is no single-touch fallback, unlike the
  double-tap path below. `MULTI_TOUCH` is declared statically in the capability set regardless of
  root, so preflight admits `gestures_multitouch` on adb; the root check is enforced at actuation
  time, not in the capability set.

### Screenshots, lifecycle, and permissions

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

### Evidence and network

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

## Flutter (via the native backends)

Flutter apps are driven by the **existing XCUITest / adb backends, unchanged** — Flutter adds no
backend of its own (roadmap
[BE-0008](../roadmaps/BE-0008-flutter-support/BE-0008-flutter-support.md)). Flutter renders its own
pixels through Skia / Impeller, but the native backends never read pixels: they read the OS
accessibility tree, and Flutter maintains a semantics tree that its engine bridges into that tree
(Android's `AccessibilityBridge` turns each `SemanticsNode` into a virtual `AccessibilityNodeInfo`;
the iOS engine exposes `UIAccessibility` elements). A widget that sets
`Semantics(identifier: …)` therefore surfaces as a resolvable element on both backends, and a
bounds-center tap lands via the semantics node's on-screen rect and Flutter's own hit-testing. The
selector model, machine assertions, and the runner stay byte-for-byte unchanged.

The id convention, alongside the iOS and Android ones above (Flutter **3.19+**, when
`SemanticsProperties.identifier` began mapping into the platform tree):

| `Selector` field | Flutter (via the native backend) |
|---|---|
| `id` (primary) | `Semantics(identifier: "…")` → `accessibilityIdentifier` (iOS) / `resource-id` (Android) |
| `label` (auxiliary) | the widget's semantics label (visible text) |
| `value` | the widget's semantics `value` (the state mirror, `Semantics(value: …)`) |
| `traits` (role filter) | the semantics role surfaced as the platform widget class / trait (`button`, `selected`, …) |

Two preconditions the app must meet — they are about Flutter's semantics state, not the renderer:

- **Semantics is built lazily.** Flutter constructs the tree only once an accessibility client
  connects or the app calls `SemanticsBinding.instance.ensureSemantics()`. On both backends the
  connection triggers construction on its own — Android's UI Automator connects as an accessibility
  service, and, as this item verified on device, **the XCUITest runner's accessibility query
  triggers it on iOS too**, so no `ensureSemantics()` call is needed for the driven path. The
  showcase app keeps the call behind an off-by-default `--dart-define=ENSURE_SEMANTICS=true` as a
  documented fallback for an app that is driven without an accessibility client.
- **Only widgets that carry semantics appear.** Standard Material / Cupertino widgets and text carry
  semantics automatically; a `CustomPaint`-drawn control that is not wrapped in `Semantics` never
  enters the tree. Wrapping it in `Semantics(identifier: …)` is the same convention that surfaces
  the id. Flutter draws its own navigation chrome, so the app also sets the back control's
  identifier to the platform convention `BackButton` (`base.OS_BACK_BUTTON`) that the iOS backend's
  `back` step taps; on Android the system back key pops as usual.

**Verified on device** by the `showcase-flutter` (iOS, XCUITest) and `showcase-flutter-android`
(Android, adb) targets, a Flutter twin of the native showcase apps
([`demos/showcase/flutter`](../demos/showcase/flutter)) that the shared `scenarios/` set drives
unchanged — id-based selectors, `value` assertions over the state mirror, scroll-to-element over the
lazily-built (culled) Notices list, and native two-finger `pinch` / `rotate`. Run it with
`make -C demos/showcase run-flutter` (iOS) / `run-flutter-android` (Android).

Out of scope (see the roadmap item for the reasoning):

- **Features that need the in-app collector / receiver library the Flutter twin does not link.**
  Two capabilities depend on `BajutsuKit` (iOS) / `BajutsuAndroid` (Android) being linked into the
  app — which the Flutter app is not, to stay plugin-free:
  - **`network` capture and `mocks`** route app traffic through an in-app interceptor (`BajutsuKit`
    `URLProtocol` on iOS, `BajutsuAndroid`'s OkHttp interceptor on Android). Flutter's Dart
    `HttpClient` flows through neither, so `network` evidence and `mocks` do not observe Flutter
    traffic; the app's `*.status` mirror still drives the deterministic wait/assert the scenarios
    rely on. Routing Dart HTTP through the native stack (via `cupertino_http` / `cronet_http`) is a
    follow-up.
  - **The Android device-control `clipboard`** round-trips through `BajutsuAndroid`'s in-app receiver
    (BE-0233), so `device.yaml`'s `setClipboard` / `clipboard` steps fail on the Flutter Android
    target. iOS device-control clipboard goes through simctl with no app cooperation, so it works on
    the Flutter iOS target — this gap is Android-only.

  Beyond these, the Flutter targets pass the same on-device scenario sets the native twins run —
  minus what is platform-limited regardless of Flutter: multi-touch (`gestures_multitouch`) needs a
  rooted emulator on adb (as for the native Android apps), and `text_editing` / the `push` half of
  `device` are iOS-only in the native suite too.
- **Deeplink-to-tab routing.** The Flutter targets register the per-flavor scheme (the Android
  `VIEW` intent-filter, the iOS `CFBundleURLTypes`, each target's `deeplinkScheme`), but — unlike the
  native twins, which route the URI to a tab (select it, pop to root, dismiss any modal) — the
  Flutter app does not yet handle the incoming URI. The scheme is registered so the BE-0007
  deeplink-actuation follow-up can drive `am start -a VIEW -d <scheme>://<tab>` / `simctl openurl`;
  the app-side handler lands with that slice. No shared scenario drives a literal-scheme deeplink
  today (`navigation.yaml` / `notices.yaml` use launch env and taps only), so the on-device
  verification is unaffected.
- **Flutter Web (CanvasKit).** It paints to a canvas and surfaces no DOM elements, so the Playwright
  backend cannot resolve them.
- **The iOS `noax` twin.** The a11y build is the surfacing proof; a distinct no-identifier iOS bundle
  needs Flutter-flavor bundle-id separation, a follow-up. The Android `noax` twin ships
  (`showcase-flutter-android-noax`), built via a Gradle product flavor.

## Playwright (web)

Headless Chromium via Playwright (Python). Runs on Linux with **no Mac and no Simulator**, so it
fits the same toolchain as `make check`. Implementation: `common/drivers/playwright.py` (roadmap
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
Implementation: `common/drivers/fake.py`.

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

Implementation: `bajutsu/common/backends.py`.

```python
PLATFORMS = {                              # a platform token expands to its actuators (stability order)
    "ios":     ("xcuitest",),              #   the sole iOS actuator since BE-0290 retired idb
    "android": ("adb",),                   #   the sole Android actuator (BE-0007)
    "web":     ("playwright",),            #   implemented (BE-0041)
    "fake":    ("fake",),                  #   the in-memory test/demo driver
}
COST_ORDER: dict[str, tuple[str, ...]] = {}  # empty: no platform's cost order differs from its stability order
IMPLEMENTED = {"fake", "playwright", "xcuitest", "adb"}  # actuators with a driver today

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

Implementation: `bajutsu/common/backend_cli/simctl.py`. Command builders are pure functions (unit-tested); execution goes
through an injectable `RunFn`.

| Method | Command | Notes |
|---|---|---|
| `erase()` | `simctl erase <udid>` | clean environment |
| `boot()` | `simctl boot <udid>` | idempotent if already booted (swallows the error) |
| `launch(bundle, args, env)` | `simctl launch --terminate-running-process <udid> <bundle> <args>` | env injected via `SIMCTL_CHILD_*` |
| `terminate(bundle)` | `simctl terminate <udid> <bundle>` | ignored if not running |
| `openurl(url)` | `simctl openurl <udid> <url>` | deeplink |
| `screenshot(path)` | `simctl io <udid> screenshot <path>` | — |

> **Every call is bounded** ([BE-0363](../roadmaps/BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md)):
> the shared runner passes a deadline to every one-shot `simctl` subprocess, chosen from the command
> itself — a long one for the commands whose duration the device or the app sets rather than simctl
> (`bootstatus`, `boot`, `erase`, `install`), a short one for everything else. A call that never returns, the observable symptom of a
> wedged CoreSimulator, therefore raises `simctl.DeviceTimeout` naming the command and the deadline
> it exceeded, instead of hanging until CI cancels the whole job with no cause. `DeviceTimeout`
> subclasses `DeviceError`, so a handler that already converts a device fault needs no change. Where
> it lands differs by caller: the best-effort probes (`device_booted`, `device_available`,
> `device_catalog`, and the rest) fold it into their documented fallback and log it, so the recovery
> ladder still decides on what it observed; every other call raises, including the idempotent
> `shutdown` / `boot` / `uninstall` / `terminate`, whose suppressions absorb a *failing* call and not
> a hanging one.

> **Injecting launch env**: an env var to pass to the app is set on the parent process as
> `SIMCTL_CHILD_<NAME>`, which reaches the child (the app) as `<NAME>`. `child_env()` does this
> conversion. The showcase's launch hooks like `SHOWCASE_UITEST` use this mechanism
> ([showcase](showcase.md#launch-environment-hooks)).

The `video` / `deviceLog` interval captures also use `simctl io recordVideo` / `simctl spawn log
stream`, but those live in the evidence subsystem (`evidence/intervals.py`)
([evidence](evidence.md#interval-evidence-video--devicelog--apptrace)).
