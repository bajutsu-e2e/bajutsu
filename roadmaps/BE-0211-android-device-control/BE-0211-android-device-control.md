**English** · [日本語](BE-0211-android-device-control-ja.md)

# BE-0211 — Android device control (setLocation, clipboard)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0211](BE-0211-android-device-control.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0211") |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md) |
<!-- /BE-METADATA -->

## Introduction

The Android backend ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) has no
device control: `AndroidEnvironment.device_control()` returns `None` (`bajutsu/platform_lifecycle.py`),
so every device-control step (`setLocation`, `setClipboard`, …) fails as `UnsupportedAction`. The
Android emulator can honor a subset of the family — `setLocation` via `emu geo fix`, and the
clipboard read/write/clear operations. This item implements an `AndroidDeviceControl` for exactly
that subset and wires it into the environment, reaching parity with idb where the emulator can back
the operation.

## Motivation

On iOS the `DeviceControl` Protocol is fully backed by simctl (`bajutsu/platform_lifecycle.py`),
so `setLocation` and clipboard steps run. On Android they cannot run at all today, which leaves a
class of scenarios (location-dependent flows, clipboard paste flows) unrunnable on the second-most-
common mobile target. The emulator exposes the needed operations through the adb / emulator console,
so the gap is implementation, not a platform limitation — for the subset the emulator supports.

The operations the emulator does **not** support (`push`, `clearKeychain`, the status-bar
overrides) must stay honestly unsupported, which is why this item depends on per-operation
capability tokens rather than the coarse `deviceControl` token: advertising the coarse token would
green-light an unsupported `push` at preflight. That token split is authored as a separate item in
this same batch (the granular device-control capabilities item); this item declares its supported
subset against those tokens.

## Detailed design

### The supported operations

| Operation | Android mechanism |
|---|---|
| `setLocation` | `emu geo fix <lon> <lat>` (emulator console over adb) |
| `setClipboard` / `getClipboard` / `clearClipboard` | `cmd clipboard` (`set-primary-clip` / `get-primary-clip` / `clear-primary-clip`) |

`push`, `clearKeychain`, `overrideStatusBar`, `clearStatusBar`, `background`, `foreground` have no
faithful emulator equivalent and remain unsupported (not advertised, so preflight fails them early).

### Work breakdown (MECE)

1. **adb command builders** (`bajutsu/adb.py`). Pure builders for the supported operations —
   `emu geo fix`, and the `cmd clipboard` set/get/clear — through the existing `_adb()`
   serial-validation helper, the twin of the simctl builders.
2. **`AndroidDeviceControl`** (`bajutsu/platform_lifecycle.py`). A class implementing the
   `DeviceControl` Protocol for the supported operations, forwarding the environment's injected
   runner, mirroring the idb `_Control` shape.
3. **Wire the environment**. `AndroidEnvironment.device_control()` returns the new control instead
   of `None`; the `getClipboard` read-back path feeds the `clipboard` assertion as on idb.
4. **Declare the supported subset** against the per-operation capability tokens (from the granular
   device-control capabilities item), so preflight passes `setLocation` / clipboard and fails the
   rest early.
5. **Validation**. Fast-gate tests over an injected runner: command shape for each operation, the
   clipboard read-back round-trip, and that preflight admits the supported subset while rejecting
   the unsupported operations. On-device confirmation rides the Android emulator e2e lane.

## Alternatives considered

- **Implement clipboard by driving the UI (paste menu).** Rejected: brittle and app-dependent,
  the opposite of a deterministic device-state operation; `cmd clipboard` sets the system clipboard
  directly.
- **Advertise nothing until the whole family is reachable.** Rejected: it needlessly blocks
  `setLocation` and clipboard, which the emulator supports today. The per-operation tokens exist
  precisely so a backend can ship the subset it can honor.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] adb command builders for `emu geo fix` and `cmd clipboard` (`bajutsu/adb.py`).
- [ ] `AndroidDeviceControl` over the `DeviceControl` Protocol subset (`bajutsu/platform_lifecycle.py`).
- [ ] Wire `AndroidEnvironment.device_control()` and the clipboard read-back.
- [ ] Declare the supported subset against the per-operation capability tokens.
- [ ] Validation — fast-gate tests (command shape, clipboard round-trip, preflight admit/reject).

## References

[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0128 — Preflight-gate device-control steps by capability](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md),
`bajutsu/adb.py`, `bajutsu/platform_lifecycle.py`
