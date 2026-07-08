**English** · [日本語](BE-0199-doctor-screen-probe-dedupe-ja.md)

# BE-0199 — Share the doctor screen probe between CLI and serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0199](BE-0199-doctor-screen-probe-dedupe.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0199") |
| Implementing PR | [#814](https://github.com/bajutsu-e2e/bajutsu/pull/814) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

The doctor's "grab the current screen" probe is implemented twice: once for the CLI
(`bajutsu/cli/commands/doctor.py`, `_current_screen`) and once for the serve Web UI panel
(`bajutsu/serve/operations/doctor.py`, `_current_screen`). The two are near-verbatim copies
maintained independently, and the check sets around them have already drifted. This item
extracts one shared probe and reconciles the drift.

## Motivation

The serve copy's docstring says it plainly: it mirrors `cli.commands.doctor._current_screen`.
The Playwright branch (build the driver, `navigate()`, `query()`,
`contextlib.suppress(*_playwright_error_types())` on close) is verbatim in both; both do the
xcuitest → idb fallback and the `resolve_udid` tail. The differences are exactly the injectable
parts: the serve side passes `state.simctl`, handles the `fake` backend, and takes the first
udid of a comma-separated list; the CLI raises `typer.Exit` on a missing `baseUrl` where serve
raises `ValueError`.

Beyond the copy itself, the drift the duplication invites is already visible. The CLI's check
assembly merges xcuitest and idb runnability and adds the idb version-pin check
(`bajutsu/cli/commands/doctor.py:104-121`); the serve panel
(`bajutsu/serve/operations/doctor.py:86-100`) has
neither, so the Web UI's doctor silently reports less than the CLI's for the same target. A
shared probe (and a shared check assembly where the surfaces agree) is exactly the fix that
prevents the next divergence.

## Detailed design

1. Add one shared screen probe to `bajutsu/doctor.py` with the environment injected: a `RunFn`
   for simctl, a flag/hook for the `fake` backend, and udid normalization. It raises a typed
   error (e.g. `DoctorProbeError`) instead of a transport-specific one.
2. The CLI adapter maps the typed error to `typer.Exit(2)` and keeps its current UX.
3. The serve adapter passes `state.simctl`, keeps its comma-list udid handling, and maps the
   typed error to its existing `ValueError` surface.
4. Reconcile the check-set drift as an explicit decision: the serve panel gains the merged
   xcuitest + idb runnability view and the idb version-pin check, unless a reason to keep the
   panel narrower is found (record the outcome here either way).
5. Cover the shared probe with unit tests (fake driver + injected `RunFn`), so the two adapters
   only need thin surface tests.

## Alternatives considered

- **Leave the copies.** The drift in the check assembly shows where this goes: the two doctors
  keep answering "is this target healthy?" with different thoroughness, and every future check
  lands on one side only.
- **Share only small helpers (udid parsing, error types) and keep two probes.** The verbatim
  Playwright branch and the backend-fallback logic are the risky parts; sharing only the
  periphery leaves the actual duplication in place.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Shared probe in `bajutsu/doctor.py` with injected environment (`simctl_run`) and a typed error (`DoctorProbeError`)
- [x] CLI adapter (typer.Exit mapping) migrated
- [x] Serve adapter (state.simctl, comma-list udid) migrated
- [x] Check-set drift reconciled: shared `preflight.doctor_environment_checks` gives the serve panel the CLI's xcuitest→idb merge and idb version-pin check (decision: widen serve to the CLI's fuller view)
- [x] Unit tests for the shared probe and the shared check assembly

**Log**

- Shared `doctor.probe_screen` (+ `DoctorProbeError`, `_first_udid`) and `preflight.doctor_environment_checks` extracted; CLI and serve doctors reduced to thin adapters over them, and the `ios_pin` accessor pulled up as `config.idb_version_pin`. The serve panel now reports the same environment checks as the CLI. The extraction also corrected two latent bugs it subsumed: the CLI `fake` backend no longer resolves a udid (would have shelled out to `xcrun`), and serve resolves adb serials via `adb.resolve_serial` rather than `simctl.resolve_udid`.

## References

- [`bajutsu/cli/commands/doctor.py`](../../bajutsu/cli/commands/doctor.py) · [`bajutsu/serve/operations/doctor.py`](../../bajutsu/serve/operations/doctor.py) · [`bajutsu/doctor.py`](../../bajutsu/doctor.py)
- [BE-0148](../BE-0148-serve-doctor/BE-0148-serve-doctor.md) — the serve doctor panel whose probe copy this consolidates
