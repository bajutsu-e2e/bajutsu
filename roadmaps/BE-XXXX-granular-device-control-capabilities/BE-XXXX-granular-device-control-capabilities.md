**English** · [日本語](BE-XXXX-granular-device-control-capabilities-ja.md)

# BE-XXXX — Split the coarse deviceControl capability into per-operation tokens

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-granular-device-control-capabilities.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) |
<!-- /BE-METADATA -->

## Introduction

`deviceControl` (`bajutsu/drivers/base.py`) is a single capability token that stands for the whole
simctl `DeviceControl` family — `setLocation`, `push`, `clearKeychain`, `clearClipboard`,
`setClipboard`, `getClipboard`, `background`, `foreground`, `overrideStatusBar`, `clearStatusBar`.
[BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md)
gates every device-control step on this one token. That treats the family as all-or-nothing, which
is correct only while every backend either supports all of it (idb) or none of it (fake, web). This
item splits the coarse token into per-operation capability tokens, so preflight can gate each
device-control step on exactly the operation it needs.

## Motivation

A backend that supports part of the family breaks the all-or-nothing assumption, and the Android
adb backend ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)) is the first such
backend. The emulator can honor `setLocation` (`emu geo fix`) and the clipboard operations, but has
no equivalent of `push`, `clearKeychain`, or the status-bar overrides. With a single token there is
no honest choice:

- Advertise `deviceControl` and preflight green-lights a `push` step that then fails at runtime —
  the exact late-failure mode
  [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md) and
  [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md)
  were built to eliminate.
- Withhold `deviceControl` and preflight blocks a `setLocation` step the emulator would have run.

The coarse token thus reintroduces late failure for any backend that partially satisfies the
family. Per-operation tokens let a backend declare precisely what it can do, and preflight gate each
step against that — keeping the fail-fast guarantee intact as backends multiply. This is a
determinism-and-diagnostics change to the deterministic `run`/CI path, with no LLM involved.

## Detailed design

The change is confined to the capability layer and the preflight mapping; the step handlers and the
`DeviceControl` Protocol are untouched.

### Work breakdown (MECE)

1. **Define per-operation tokens** (`bajutsu/drivers/base.py`). Replace the single `DEVICE_CONTROL`
   token with a token per operation (or per cohesive sub-group where operations always ship
   together, e.g. the clipboard read/write/clear trio). The grouping is the one design decision:
   split far enough that every backend's real support is expressible, no finer.
2. **Map each step to its token** (`bajutsu/capability_preflight.py`). The single `deviceControl`
   gate becomes a per-step lookup, so an unsupported operation is named individually in the
   aggregated preflight message BE-0128 already emits.
3. **Declare support per backend**. idb advertises the full set (behavior byte-for-byte unchanged);
   fake and the web backend are unchanged (they declare none). No new backend support is added here
   — this item only makes partial support *expressible*.
4. **Preserve the runtime gate**. The per-step `_need_control` guard in the handlers still raises
   `UnsupportedAction` as the backstop, so the change is preflight-precision only, never a
   behavior regression for a supported step.
5. **Validation**. Fast-gate tests: a backend advertising a subset passes preflight for a supported
   operation and fails fast for an unsupported one, with the offending step named; idb's full-set
   behavior is unchanged.

This item is the enabler for the Android device control item (which implements the emulator-backed
`setLocation` / clipboard operations against the tokens defined here); that item is authored
separately in this same batch.

## Alternatives considered

- **Keep the coarse token; let Android advertise nothing.** Rejected: it blocks `setLocation` and
  clipboard, which the emulator supports, so scenarios that would run are refused — the opposite
  failure, and still dishonest about the backend's real capability.
- **Try each operation at runtime and downgrade on failure.** Rejected: it violates determinism
  first (BE-0082's whole point) — the run would do partial device work before discovering the step
  is unsupported.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Define per-operation tokens (`bajutsu/drivers/base.py`).
- [ ] Map each device-control step to its token (`bajutsu/capability_preflight.py`).
- [ ] Declare support per backend (idb full set; fake / web unchanged).
- [ ] Preserve the runtime `_need_control` backstop.
- [ ] Validation — fast-gate preflight tests over a subset-advertising backend.

## References

[BE-0128 — Preflight-gate device-control steps by capability](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md),
[BE-0082 — Preflight capability check before a run](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md),
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
`bajutsu/drivers/base.py`, `bajutsu/capability_preflight.py`
