**English** · [日本語](BE-0042-platform-backend-registry-ja.md)

# BE-0042 — Platform-aware backend registry & selection

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0042](BE-0042-platform-backend-registry.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0042") |
| Implementing PR | predates the per-PR history (part of the initial import; no single PR) |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

The first slice of the multi-platform direction ([BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)) has already landed.
Backend selection now keys off a **platform registry**, not a single hard-coded iOS [actuator](../../docs/glossary.md#driver-backend-actuator-platform).

`--backend` and the config `backend:` field accept two kinds of token:

- a **platform token** (`ios` / `android` / `web` / `fake`)
- a bare actuator name (for example `idb`)

A platform expands to its actuators in stability order.
The chosen actuator is the first one that is **implemented and available** in this environment.

This is the selector-side groundwork for a real second platform.
The platform can then slot in without touching scenarios, the config schema, or the deterministic core.

## Motivation

Going multi-platform means adding a per-platform triple behind the existing `Driver` seam.
The triple is an actuator, an environment manager, and a stable-id convention.
The deterministic spine stays unchanged ([BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)).

Before any of that, backend *selection* had to stop assuming "iOS = idb".
It needed to do three things:

1. Name platforms as first-class tokens.
2. Map a platform to an ordered list of candidate actuators.
   A richer iOS actuator such as XCUITest can then be preferred over `idb` later without a config change (see [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)).
3. Report a *clear* "not implemented yet" for a recognized-but-unbuilt platform, instead of a generic failure.

Landing this slice first keeps later per-platform work additive.

## Detailed design

The implementation lives in [`bajutsu/backends.py`](../../bajutsu/backends.py).
See also [drivers → backend selection](../../docs/drivers.md#backend-selection-and-the-actuator).

A platform registry maps each platform token to its actuators, most-stable-first:

```python
PLATFORMS = {
    "ios":     ("idb",),   # later: ("xcuitest", "idb")
    "android": ("adb",),
    "web":     ("playwright",),
    "fake":    ("fake",),
}
```

- **Expansion.** A platform token expands to its actuators, and a bare actuator stands for itself.
  `--backend ios` (or `backend: [ios]`) resolves to `idb` today.
  It will pick up a richer iOS actuator when one lands, with no change to the scenario or config.
- **Selection.** `select_actuator` walks the expanded list and returns the **first implemented and available** actuator.
  Availability means "implemented **and** its executable is on `PATH`".
  `fake` is always available and needs no executable.
  Today `IMPLEMENTED = {idb, fake}`.
- **Clear errors for planned platforms.** The registry recognizes `android` and `web`, but they have no driver yet.
  Requesting either raises a `"not implemented yet"` error that points at the Platform support roadmap.
  This error is distinct from "no available actuator among …".
  Constructing such a driver via `make_driver` likewise raises `NotImplementedError`, not a generic failure.
- **Forward-compatible.** Genuinely unknown tokens are skipped, not failed.
  An older build can therefore run a config that lists a future backend.
  The config falls through to a backend that the older build understands.

The matching configuration shape is a **`platform` discriminator** on `apps.<name>`, plus per-platform target fields.
The deterministic resolution order (`defaults < app < scenario`) is unchanged:

```yaml
defaults:
  platform: ios                 # default; per-app override below

apps:
  sample-ios:
    platform: ios
    backend:  [idb]
    bundleId: com.bajutsu.sample
  sample-android:
    platform: android
    backend:  [adb]
    package:  com.bajutsu.sample          # bundleId's peer
  sample-web:
    platform: web
    backend:  [playwright]
    baseUrl:  https://app.example.test     # bundleId's peer
```

`platform` selects which **environment manager** and **backend registry** are in play.
The rest of the schema (namespaces, redact, setup, capture) stays shared.

**What is done and what remains**.
Landed: the platform registry, platform-token expansion, implemented-and-available selection, and the clear planned-but-absent errors.
`idb` and `fake` are the only implemented actuators today.

A real second platform still needs the **rest of the triple**:

- a per-platform **environment manager** (a `simctl` peer)
- the **actuator driver**: `adb` for [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), and `playwright` for [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)

It also needs the explicit `platform` config field described above.

## Alternatives considered

- **A single `backend` string that names only an actuator** (the pre-existing shape).
  Rejected.
  The string cannot express "prefer XCUITest, fall back to idb" or "this is the Web platform".
  Expressing either would leak actuator choices into every config and scenario.
- **Failing hard on any unrecognized token.**
  Rejected for forward-compatibility.
  A config authored for a future build should still run on an older one.
  It does so by falling through to a backend that the older build understands.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[`bajutsu/backends.py`](../../bajutsu/backends.py), [drivers.md](../../docs/drivers.md#backend-selection-and-the-actuator), [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md), [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
