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
| `semanticTap` | tap directly by id/label (no coordinates) | — | — | ✅ |
| `conditionWait` | native condition waiting | — | — | ✅ |
| `network` | native network monitoring | — | — | — |
| `multiTouch` | two-finger gestures (pinch / rotate) | — | — | ✅ |

> Both real backends actuate by **frame-center coordinates** — neither exposes a usable semantic tap
> on a real device (see RocketSim below), so the run loop resolves a unique element via `query()` and
> taps its center. `pinch` / `rotate` raise `UnsupportedAction` on both (single-touch); those go
> through codegen → XCUITest.

## RocketSim

Local backend (the RocketSim GUI app must be running). Implementation: `drivers/rocketsim.py`.

**Verified on-device (2026-06):** RocketSim's `rs/1` agent protocol exposes role / label / value /
frame and an *ephemeral* element id — but **no accessibilityIdentifier**. So, unlike idb, RocketSim
cannot resolve bajutsu's id-first selectors on its own. Two consequences:

1. Identifiers are recovered by an **[idmap](#identifier-recovery-idmap)** applied in `query()`.
2. Actuation is by **frame-center coordinates** (`rocketsim interact tap <x> <y>`), not the `--id`
   semantic tap (that `--id` is the ephemeral id, useless across snapshots).

- `query()`: parses `rocketsim elements --agent-mode debug --udid <udid>`
  (`{ data: { elements: [...] } }`, frames as `[[x,y],[w,h]]`) via `parse_elements`, then applies the
  app's idmap to fill identifiers.
- `tap(sel)`: `resolve_unique` → tap the frame center via `rocketsim interact tap`.
- `type_text` / `swipe` / `long_press` → `rocketsim interact type|swipe|long-press`; `screenshot`
  uses `simctl io` (reliable, same as idb).
- A concrete UDID is required (`booted` is a simctl-only alias; the run pipeline resolves it via
  `env.resolve_udid`).

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

## Identifier recovery (idmap)

Implementation: `bajutsu/idmap.py`. Per-app, optional (`apps.<name>.idMap` in config, a path relative
to the config file).

idb's `describe-all` carries `AXUniqueId` (= accessibilityIdentifier), so id-first selectors resolve
directly. RocketSim's protocol has no identifier at all — only role / label / value. An **idmap**
bridges the gap: a table mapping each accessibilityIdentifier to a matcher against what RocketSim
*does* report.

```yaml
# sample/sample.idmap.yaml
home.title:        { role: staticText, label: "Home" }      # role splits title vs the "Home" tab
counter.value:     { role: staticText, labelMatches: "^Count:" }  # regex for dynamic text
counter.increment: { role: button, label: "+" }
home.search:       { role: textField }                       # the only text field on the screen
list.row.3:        { role: staticText, label: "Item 3" }
```

`apply(elements, idmap)` fills the identifier on each element whose identifier is unset, but **only
when a matcher resolves to exactly one** such element — ambiguous or absent matches are left
unresolved so the selector layer reports "no match / ambiguous" instead of guessing. A backend that
already provides identifiers (idb) is therefore unaffected. The same id-first scenario then runs on
both backends.

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
def make_driver(backend, udid, idmap=None) -> Driver:  # "rocketsim" → RocketSimDriver (uses idmap), "idb" → IdbDriver
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
