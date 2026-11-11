**English** · [日本語](BE-0158-timezone-device-primitive-ja.md)

# BE-0158 — Timezone device primitive

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0158](BE-0158-timezone-device-primitive.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Deferred** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0158") |
| Topic | Scenario authoring features |
| Related | [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md) |
| Origin | MagicPod |
<!-- /BE-METADATA -->

## Introduction

This item covers pinning the Simulator's **timezone**. Timezone was one of the device-state
primitives that
[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
proposed. BE-0052's implementation triage found no reliable, deterministic actuator for it. The
primitive moves here as its own deferred item. The shake gesture has a different blocker and is
tracked in its own item. This item waits on a verified mechanism.

## Motivation

Testing date-dependent UI across timezones requires that the test can pin the device's zone. Such UI
includes a "Today" header, a countdown, and a scheduling screen. Without pinning, the result drifts with where
CI happens to run. The same scenario can pass on one machine and fail on another purely because of
the host's local time. Pinning the zone deterministically would also close an easy "Maestro can,
Bajutsu can't" objection. Maestro ships a broad device-control vocabulary, and Bajutsu already
matches it for most of BE-0035's primitives.

Authoring effort is not what blocks this item. The block is the absence of a deterministic,
per-device actuator. A primitive that appears to run but leaves the UI reading the wrong zone is
worse than no primitive. It produces a green run that proves nothing, which is exactly what
determinism-first (prime directive #2) forbids.

## Detailed design

The proposed surface is unchanged from BE-0052:

```yaml
- setTimezone: { id: "Asia/Tokyo" }          # pin the device timezone
```

**Why it is blocked.** The Simulator has no per-device timezone control:

- `simctl` has no `timezone` subcommand, and neither does `idb`. The Simulator's timezone is
  **inherited from the host Mac** (`/etc/localtime`), so a booted device reports the host's zone.
- The sole actuator that moves the whole device is changing the **host Mac's** timezone
  (`sudo systemsetup -settimezone`, or rewriting `/etc/localtime`). That approach is global, needs
  `sudo`, affects every Simulator at once, and mutates the developer's or CI machine's clock. It
  breaks per-scenario and per-device isolation and is out of
  contract.
- A launch-time `SIMCTL_CHILD_TZ` (or a `TZ` launch-env) sets the app process's C-library
  `localtime` alone. Most iOS date UI reads `TimeZone.current` / `NSTimeZone.systemTimeZone` (Core
  Foundation), which **ignores `TZ`**. The step "runs" but the UI does not change. That is the worst
  kind of silent no-op.
- The Simulator's GUI menus have no timezone item, so GUI automation (AppleScript, RocketSim) does
  not help either. The companion *Shake device primitive* item explains why that path is itself
  constrained.

A viable mechanism would have to move the device-wide `TimeZone.current` for one Simulator, from the
command line, without touching the host. None is known today.

**Prime directives.** A future implementation must meet three conditions:

- Keep the `run`/CI gate AI-free (directive #1).
- Actuate deterministically, with a machine-checkable result and no settle-sleep (directive #2).
- Keep the timezone id in the scenario or `apps.<name>` config, not in tool code (directive #3).

## Alternatives considered

- **Ship via `SIMCTL_CHILD_TZ` at launch.** Rejected. It moves libc `localtime` and not the
  `TimeZone.current` that most iOS date UI reads. It is a silent no-op for the very UI it is meant
  to test, which is a determinism-first violation.
- **Ship by changing the host Mac's timezone.** Rejected. The change is global, `sudo`-gated, and
  affects all Simulators. It also mutates the host/CI clock. It breaks device isolation and is out
  of contract.
- **Approximate it from inside the app (a launch flag that fixes the timezone).** Rejected as the
  primary mechanism. It pushes the burden onto every target app and breaks app-agnosticism. Launch
  env remains available for genuinely app-specific setup.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] A verified per-device, host-independent, headless timezone actuator that moves `TimeZone.current`.

Carved out of [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
during its implementation triage; waits on a verified mechanism.

## References

Split out of [BE-0052 — Device-state primitives: timezone, clipboard, shake](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md),
itself split from [BE-0035 — Device-control steps](../BE-0035-device-control-primitives/BE-0035-device-control-primitives.md).
[DESIGN §6.2](../../DESIGN.md), `bajutsu/orchestrator/actions/handlers/device.py`, `bajutsu/env.py`
