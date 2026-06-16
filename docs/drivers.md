**English** · [日本語](ja/drivers.md)

# Driver abstraction, backends, and environment management

> One `Driver` interface, behind which sit the backends (idb, plus the in-memory `fake` for
> tests), with capability differences absorbed on the abstraction side. idb is the only real
> backend today, but the interface is designed to accommodate additional backends. Launching the app
> (boot/launch) is handled by a `simctl` wrapper.
>
> Implementation: `bajutsu/drivers/` (`base.py` / `idb.py` / `fake.py`) ·
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
    def wait_for(self, sel: Selector, timeout: float) -> bool: ...
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...          # provided capabilities (for actuator / fallback resolution)
```

> **About `wait_for`**: it exists on the Protocol, but the run loop's condition waits are done by
> the orchestrator itself polling `query()` (`_wait`, [run-loop](run-loop.md#waits-condition-waits-only)),
> so the current execution path does not call the driver's `wait_for` directly. It remains part
> of the interface.

### Capabilities (`Capability`)

The set of tokens returned by `capabilities()`, used for actuator selection and evidence fallback
resolution.

| Capability | Meaning | idb | fake |
|---|---|:--:|:--:|
| `query` | element-tree query | ✅ | ✅ |
| `elements` | element-dump evidence | ✅ | ✅ |
| `screenshot` | screenshot | ✅ | ✅ |
| `semanticTap` | tap directly by id/label (no coordinates) | — | ✅ |
| `conditionWait` | native condition waiting | — | ✅ |
| `network` | native network monitoring | — | — |
| `multiTouch` | two-finger gestures (pinch / rotate) | — | ✅ |

> idb actuates by **frame-center coordinates** — it exposes no semantic tap, so the run loop resolves
> a unique element via `query()` and taps its center. `pinch` / `rotate` raise `UnsupportedAction`
> (single-touch); those go through codegen → XCUITest. The `fake` driver advertises a richer
> capability set (semanticTap / conditionWait / multiTouch) purely to exercise those code paths in
> tests.

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
> fb-idb (iPhone 17 Pro, recent iOS) via `make -C demos/features e2e` + the `e2e.yml` CI workflow; re-check them only
> if the installed idb version changes the schema (the note atop `idb.py`). The idb client is
> `uv sync --extra idb`; `idb_companion` is `brew install facebook/fb/idb-companion`.

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
    "ios":     ("idb",),                   #   later: ("xcuitest", "idb")
    "android": ("adb",),                   #   planned
    "web":     ("playwright",),            #   planned
    "fake":    ("fake",),                  #   the in-memory test/demo driver
}
IMPLEMENTED = {"idb", "fake"}              # actuators with a driver today

def default_available(actuator) -> bool:   # implemented and its executable on PATH (fake: always)
def resolve_actuators(backends) -> list:   # expand each token (platform or actuator) to actuators
def select_actuator(backends, available) -> str:  # first implemented + available, in order
def make_driver(actuator, udid) -> Driver: # "idb" → IdbDriver, "fake" → FakeDriver
```

- A **backend token** is either a **platform** (`ios` / `android` / `web` / `fake`) or a concrete
  **actuator** (e.g. `idb`). `--backend ios` (or `backend: [ios]`) resolves to `idb` today, and would
  pick up a richer iOS actuator (XCUITest) when one lands — the scenario and config never change.
- `backend` is an **ordered list** (most-stable-first; [concepts](concepts.md#5-the-stability-ladder)).
  Each token is expanded to its actuators, in order; the **actuator = the first implemented and
  available** one. If none is available, `RuntimeError` (the CLI exits with code 2).
- `android` / `web` are **declared but not implemented yet** ([multi-platform](multi-platform.md)):
  requesting them raises a clear "not implemented yet" rather than a generic failure. Truly unknown
  tokens are skipped (forward-compat: an older build can run a config that lists a future backend).
- The availability check `available` is injectable (swappable in tests). The default is `shutil.which`
  (and `fake` needs no executable, so it is always available).
- The actuator is fixed once at the start of a run and held for the whole run (so two drivers never
  operate one device).

> The design (DESIGN §9) envisions using non-actuator backends as read-only evidence fallbacks, but
> the current execution path uses a **single actuator**; multi-backend evidence fallback is not yet
> wired up.

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
> conversion. The sample app's launch hooks like `SAMPLE_UITEST` use this mechanism
> ([sample-app](sample-app.md#launch-env-hooks)).

The `video` / `deviceLog` interval captures also use `simctl io recordVideo` / `simctl spawn log
stream`, but those live in the evidence subsystem (`intervals.py`)
([evidence](evidence.md#interval-evidence-video--devicelog)).
