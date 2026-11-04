**English** · [日本語](BE-0260-cli-bringup-consolidation-ja.md)

# BE-0260 — Consolidate the duplicated CLI command bring-up and add a neutral DeviceError

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0260](BE-0260-cli-bringup-consolidation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0260") |
| Implementing PR | [#1033](https://github.com/bajutsu-e2e/bajutsu/pull/1033) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

The commands `run`, `crawl`, `record`, and `audit` each bring up the same four pieces of device and
backend plumbing before they do their own work:

- [actuator](../../docs/glossary.md#driver-backend-actuator-platform) selection
- the target's launch server
- udid resolution
- the alert guard, where it applies

Each command also re-implements the same `try: … except RuntimeError → typer.Exit(2)` boilerplate
(or `except DeviceError`) around these pieces.

This item moves each set of copies onto one shared helper in `bajutsu/cli/_shared.py`. It also gives
`adb.DeviceError` a platform-neutral base. Generic handlers then no longer import the iOS `simctl`
module just to name the exception they catch.

## Motivation

The same bring-up logic exists as four copies, each hand-written per command.

- **Actuator selection** appears at `run.py:303-312`, `crawl.py:501-507`, `record.py:196-202`, and
  `audit.py:146-151`. Each copy has the form `try: ensure_web_runtime(...); actuator =
  select_actuator(...) except RuntimeError → typer.Exit(2)`. Each copy provisions the web runtime,
  resolves the actuator, and exits 2 on the same `RuntimeError`. Only the surrounding call sites
  differ.
- **Launch-server bring-up** appears at `run.py:504-510`, `crawl.py:286-292`, `record.py:216-220`,
  and `audit.py:163-170`. Each copy has the form `try: start_launch_server(eff, upload_exec=...)
  except RuntimeError → typer.Exit(2)`. The call, the exception, and the exit code are identical in
  all four.
- **udid resolution** appears at `run.py:515`, `record.py:211-212`, `audit.py:152-156`, and
  `crawl.py:322`. Each copy wraps `_simctl.resolve_udid(...)` in `except _simctl.DeviceError →
  typer.Exit(2)`.
- **Alert-guard construction** appears at `run.py:375` and `392`, `crawl.py:231-232`, and
  `record.py:207-208`. Each builds `ClaudeAlertLocator(ai=eff.ai, redactor=redactor)` and then takes
  `SystemAlertGuard(locator, instruction).dismiss`. These three copies are *not* byte-identical.
  `run.py`'s `_alert_guard_factory` (`run.py:363-376`) first runs a `credential_gap(eff.ai)` check.
  On a missing credential it skips constructing the locator and falls back to `None`, with a
  user-facing warning. `crawl.py` and `record.py` construct the locator unconditionally. A shared
  helper is therefore not a pure behavior-preserving lift of all three copies. Detailed design item 3
  resolves this.

A behavior change to any one of these pieces has to be applied to every copy by hand. Examples are a
different exit code, an extra log line before the exit, and a new exception to catch. A missed copy
becomes a silent inconsistency between commands, not a test failure, because each copy passes its
own command's tests independently.

A second problem is the exception hierarchy. `adb.DeviceError` subclasses `simctl.DeviceError`
(`bajutsu/adb.py:32`). Ten call sites want to catch *some* device error, not an iOS-specific one, yet
they still import `bajutsu.simctl` to name the exception:

- `crawl.py:1016`
- `cli/commands/crawl.py:189,322,365`
- `cli/commands/run.py:516`
- `cli/commands/audit.py:158,193`
- `cli/commands/record.py:242`
- `cli/commands/doctor.py:181`
- `serve/operations/doctor.py:130`

This inverts the dependency that the prime directives expect. `bajutsu` is meant to be
backend-agnostic, with the platform as a backend behind one interface. Yet a purely generic
`except DeviceError` handler cannot be written today without reaching into the iOS backend's module.

## Detailed design

The design is MECE across the two independent problems named above: the duplicated bring-up, and the
exception-hierarchy inversion. The first problem splits further into the four copied pieces.

1. **`_select_actuator_or_exit(backend, eff, engines)`** in `bajutsu/cli/_shared.py` folds the
   `ensure_web_runtime` loop, the `select_actuator` call, and the `except RuntimeError →
   typer.Exit(2)` boundary into one helper. The helper returns `(actuator, backends)` and replaces
   the four call-site copies in `run.py`, `crawl.py`, `record.py`, and `audit.py`.
2. **`_start_launch_server_or_exit(eff, *, upload_exec)`** in `bajutsu/cli/_shared.py` folds the
   `start_launch_server` call and its `except RuntimeError → typer.Exit(2)` boundary into one
   helper. The helper returns `(stop_server, exec_decision)` and replaces the four copies.
   - Call-site differences stay at the call site. For example, `crawl` uses
     `atexit.register(stop_server)`, while `run` uses `finally: stop_server()`. The helper owns
     merely the bring-up-and-exit part that all four share.
   - One difference needs an explicit decision. In `audit.py`, the `except RuntimeError` block
     (`audit.py:171-173`) also calls `shutdown()`, which tears down the already-created device-pool
     lease before `typer.Exit(2)`. The other three commands do not.
   - The helper therefore takes an optional `on_error: Callable[[], None] | None` cleanup hook, which
     runs before the exit. `audit` passes `shutdown` and keeps its teardown. The other three pass
     nothing and behave exactly as today.
3. **`_build_alert_guard(eff, redactor, instruction)`** in `bajutsu/cli/_shared.py` folds the
   `ClaudeAlertLocator` and `SystemAlertGuard(...).dismiss` construction into one helper. The helper
   returns the bound `dismiss` callable and replaces the three copies in `run.py`, `crawl.py`, and
   `record.py`.
   - The helper absorbs `run.py`'s `credential_gap(eff.ai)` branch (`run.py:363-376`). On a missing
     credential it emits the same warning and returns a no-op guard.
   - For two of the three sites this is *not* purely behavior-preserving. `crawl.py` and `record.py`
     construct the locator unconditionally today. Folding them in gives them `run`'s graceful
     no-op-on-missing-credential behavior, in place of proceeding without it.
   - The alignment is deliberate: all three AI-authoring commands should degrade the same way. This
     item calls it out so that it is a decided behavior change, not an accidental one.
4. **udid resolution stays a thin per-call-site wrapper**, not a fourth shared helper.
   - Each call site's `_simctl.resolve_udid(...)` already differs in its non-udid arguments.
   - The one shared part is the `except DeviceError → typer.Exit(2)` boundary. Once step 5 lands,
     that boundary collapses into a one-line `except device_errors.DeviceError`, so no separate
     helper is needed.
5. **`bajutsu/device_errors.py`** is a new module that defines a platform-neutral `DeviceError`. It
   carries a message, matching the existing `simctl.DeviceError` shape.
   - `simctl.DeviceError` and `adb.DeviceError` both become subclasses of it. Each keeps its own
     class for platform-specific detail, and neither subclasses the other any more.
   - The 10 generic `except _simctl.DeviceError` and `except simctl.DeviceError` call sites listed
     under Motivation switch to `except device_errors.DeviceError`, and drop their `bajutsu.simctl`
     import.
   - Call sites that genuinely need an iOS-specific `simctl.DeviceError`, if any, keep importing it
     directly.

Every new helper in `_shared.py` is behavior-preserving. Each
catches the same exception, echoes the same message, raises the same `typer.Exit(2)`, and returns the
same value shape as the code it replaces. This item moves boilerplate and does not change the
exit-code contract.

`_shared.py`'s module docstring already scopes the file to "the genuinely cross-command pieces". The
four pieces qualify, because all four already exist unchanged, module for module, in every command
file.

## Alternatives considered

- **A decorator wrapping each command's entry point.** Considered and rejected. The four pieces
  come up at different points inside each command. Actuator selection runs before config resolution
  finishes, the launch server starts after the plan is built, and the alert guard applies only where
  `--dismiss-alerts` is given. A single decorator would still need parameters that mirror the plain
  helpers. It would also make the Typer command signatures, and the exact point where each exit can
  fire, harder to read at the call site than an explicit function call.
- **Leave `adb.DeviceError` subclassing `simctl.DeviceError`.** Rejected. This hierarchy directly
  causes the `bajutsu.simctl` import at every generic handler. A third platform backend (Android is
  already implemented, and more may follow) would otherwise have to subclass `simctl.DeviceError`
  too, which compounds the inversion, or introduce its own ad hoc base.
- **Move the four helpers into `bajutsu/orchestrator.py` or a driver module instead of
  `cli/_shared.py`.** Rejected. All four are CLI-command bring-up concerns (config, Typer exit codes,
  `typer.echo`), with no role in the deterministic driver and orchestrator core. `_shared.py` already
  holds the other per-command-duplicated helpers, such as config loading and secret redaction, for
  exactly this reason.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `_select_actuator_or_exit` in `cli/_shared.py`; `run`, `crawl`, `record`, `audit` migrated
- [x] `_start_launch_server_or_exit` in `cli/_shared.py`; `run`, `crawl`, `record`, `audit` migrated
- [x] `_build_alert_guard` in `cli/_shared.py`; `run`, `crawl`, `record` migrated (`run` uses the
      inner `_build_alert_locator` to keep its shared single locator)
- [x] `bajutsu/device_errors.py` with a neutral `DeviceError`; `simctl.DeviceError` and
      `adb.DeviceError` rebased onto it; generic call sites switched off `bajutsu.simctl`

### Log

- 2026-07-14: implemented all four units ([PR #1033](https://github.com/bajutsu-e2e/bajutsu/pull/1033)). Behavior-preserving refactor except the
  one deliberate alignment — `_build_alert_guard` gives `crawl`/`record` `run`'s graceful
  no-op-on-missing-credential (moot in practice: both fail closed via `_require_ai_credential` first).
  `adb.DeviceError` became a sibling of `simctl.DeviceError`, letting the 10 generic handlers drop
  their `bajutsu.simctl` import.

## References

- [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py) — where the new helpers land, per its
  own "genuinely cross-command pieces" scope
- [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) ·
  [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py) ·
  [`bajutsu/cli/commands/record.py`](../../bajutsu/cli/commands/record.py) ·
  [`bajutsu/cli/commands/audit.py`](../../bajutsu/cli/commands/audit.py) — the four commands
  carrying the duplicated bring-up
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py) · [`bajutsu/adb.py`](../../bajutsu/adb.py) — today's
  `DeviceError` hierarchy (`adb.DeviceError` subclasses `simctl.DeviceError`)
- [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md) — decomposed
  `run`'s god-function into the same bring-up steps this item now consolidates across commands
- [BE-0205](../BE-0205-crawl-command-decomposition/BE-0205-crawl-command-decomposition.md) — applied
  the same decomposition to `crawl`, producing the second copy of each helper this item merges
