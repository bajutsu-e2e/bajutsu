**English** · [日本語](BE-0042-platform-backend-registry-ja.md)

# BE-0042 — Platform-aware backend registry & selection

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0042](BE-0042-platform-backend-registry.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Implementing PR | predates the per-PR history (part of the initial import; no single PR) |
| Topic | Platform expansion (landed slices) |
<!-- /BE-METADATA -->

## Introduction

The first slice of the multi-platform direction ([BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)) has already landed: backend selection keys off a **platform registry** rather than a single hard-coded iOS actuator. `--backend` and the config `backend:` field now accept a **platform token** (`ios` / `android` / `web` / `fake`) as well as a bare actuator name (e.g. `idb`). A platform expands to its actuators in stability order, and the chosen actuator is the first one that is **implemented and available** in this environment. This is the selector-side groundwork that lets a real second platform slot in without touching scenarios, config schema, or the deterministic core.

## Motivation

Going multi-platform means adding a per-platform triple — actuator + environment manager + stable-id convention — behind the existing `Driver` seam, while the deterministic spine stays unchanged ([BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)). Before any of that, backend *selection* had to stop assuming "iOS = idb": it needed to (1) name platforms as first-class tokens, (2) map a platform to an ordered list of candidate actuators (so a richer iOS actuator like XCUITest can later be preferred over `idb` without a config change — see [BE-0019](../../in-progress/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)), and (3) report a *clear* "not implemented yet" for a recognized-but-unbuilt platform instead of a generic failure. Landing this slice first keeps later per-platform work additive.

## Detailed design

Implemented in [`bajutsu/backends.py`](../../../bajutsu/backends.py). See also [drivers → backend selection](../../../docs/drivers.md#backend-selection-and-the-actuator).

A platform registry maps each platform token to its actuators, most-stable-first:

```python
PLATFORMS = {
    "ios":     ("idb",),   # later: ("xcuitest", "idb")
    "android": ("adb",),
    "web":     ("playwright",),
    "fake":    ("fake",),
}
```

- **Expansion.** A platform token expands to its actuators; a bare actuator stands for itself. `--backend ios` (or `backend: [ios]`) resolves to `idb` today, and will pick up a richer iOS actuator when one lands — without the scenario or config changing.
- **Selection.** `select_actuator` walks the expanded list and returns the **first implemented and available** actuator. Availability is "implemented **and** its executable is on `PATH`" (`fake` is always available and needs no executable). Today `IMPLEMENTED = {idb, fake}`.
- **Clear errors for planned platforms.** Requesting `android` / `web` — recognized in the registry but with no driver yet — raises a `"not implemented yet"` error that points at the platform-expansion roadmap, distinct from "no available actuator among …". Constructing such a driver via `make_driver` likewise raises `NotImplementedError` rather than a generic failure.
- **Forward-compatible.** Genuinely unknown tokens are skipped rather than failing, so an older build can run a config that lists a future backend (it falls through to a backend it does understand).

The matching configuration shape is a **`platform` discriminator** on `apps.<name>` plus per-platform target fields, with the deterministic resolution order (`defaults < app < scenario`) unchanged:

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

`platform` selects which **environment manager** and **backend registry** are in play; the rest of the schema (namespaces, redact, setup, capture) stays shared.

**What is done vs. what remains.** Landed: the platform registry, platform-token expansion, implemented-and-available selection, and the clear planned-but-absent errors (`idb` / `fake` are the only implemented actuators today). What remains for a real second platform is the **rest of the triple** — a per-platform **environment manager** (a `simctl` peer) and the **actuator driver** (`adb` for [BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend.md), `playwright` for [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)) — plus the explicit `platform` config field described above.

## Alternatives considered

- **A single `backend` string that names only an actuator** (the pre-existing shape). Rejected because it cannot express "prefer XCUITest, fall back to idb" or "this is the Web platform" without leaking actuator choices into every config and scenario.
- **Failing hard on any unrecognized token.** Rejected for forward-compatibility: a config authored for a future build should still run on an older one by falling through to a backend it understands.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[`bajutsu/backends.py`](../../../bajutsu/backends.py), [drivers.md](../../../docs/drivers.md#backend-selection-and-the-actuator), [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend.md), [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md), [BE-0019](../../in-progress/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
