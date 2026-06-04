**English** · [日本語](ja/drivers.md)

# Driver abstraction, backends, and environment management

> One `Driver` interface, behind which sit several backends (RocketSim / idb / fake), with
> capability differences absorbed on the abstraction side. Launching the app (boot/launch) is
> handled by a `simctl` wrapper.
>
> Implementation: `bajutsu/drivers/` (`base.py` / `rocketsim.py` / `idb.py` / `fake.py`) ·
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

| Capability | Meaning | RocketSim | idb | fake |
|---|---|:--:|:--:|:--:|
| `query` | element-tree query | ✅ | ✅ | ✅ |
| `elements` | element-dump evidence | ✅ | ✅ | ✅ |
| `screenshot` | screenshot | ✅ | ✅ | ✅ |
| `semanticTap` | tap directly by id/label (no coordinates; most stable) | ✅ | — | ✅ |
| `conditionWait` | native condition waiting | ✅ | — | ✅ |
| `network` | native network monitoring | ✅ | — | — |

## RocketSim

Has a semantic tap; the top rung of the stability ladder. For local use (a GUI must be resident).
Implementation: `drivers/rocketsim.py`.

- `query()`: normalizes the output of `rocketsim elements --agent --udid <udid>` (an array or
  `{ elements: [...] }`) via `parse_elements`.
- `tap(sel)`: first `resolve_unique` to confirm uniqueness → if the element has an `identifier`,
  `rocketsim tap --id <identifier>` (**semantic tap = most stable**); otherwise a coordinate tap at
  the frame center.
- `long_press` / `swipe` / `type_text` / `screenshot` map to the corresponding commands.

> ⚠️ **The CLI surface is "assumed"**: RocketSim's actual CLI and `rs/1` JSON schema are unconfirmed;
> the parser and command builders are to be confirmed / adjusted on a real device (the NOTE atop
> `rocketsim.py`).

## idb

Headless, coordinate-based. For CI. With no semantic tap, the abstraction resolves
**id → frame center → coordinate tap**. Implementation: `drivers/idb.py`.

- `query()`: normalizes `idb ui describe-all --udid <udid> --json` via `parse_describe_all`
  (handles both a JSON array and newline-delimited JSON, absorbing `AXLabel`/`AXValue`/`AXUniqueId`, etc.).
- `tap(sel)`: `_resolve` to confirm uniqueness (**retries not-found, fails ambiguity fast**: a
  real-device tree can be transiently empty during transitions) → `idb ui tap` (integer
  coordinates) at the frame center.
- `screenshot`: idb's own frame capture is unreliable, so it uses **`simctl io screenshot`**.
- `swipe`: adds `--duration 0.2` to make it a real drag (an instantaneous swipe is not recognized
  as a pan by SwiftUI).

> ⚠️ The describe-all JSON key names are **assumed** to match fb-idb's output and need confirmation
> against the installed idb (the note atop `idb.py`). The idb client is `uv sync --extra idb`;
> `idb_companion` is `brew install facebook/fb/idb-companion`.

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
KNOWN = ("rocketsim", "idb")               # fake is test-only; not listed here

def default_available(backend) -> bool:    # is the executable on PATH (a coarse first check)
def select_actuator(backends, available) -> str:  # the first available in stability order
def make_driver(backend, udid) -> Driver:  # "rocketsim" → RocketSimDriver, "idb" → IdbDriver
```

- `backend` is a **stability-ordered list** (more stable first; [concepts](concepts.md#5-the-stability-ladder)).
- The **actuator = the first available backend in the list**. If none is available, `RuntimeError`
  (the CLI exits with code 2).
- The availability check `available` is injectable (swappable in tests). The default is `shutil.which`.
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
