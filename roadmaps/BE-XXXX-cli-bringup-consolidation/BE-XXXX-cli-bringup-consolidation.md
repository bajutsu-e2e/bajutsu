**English** · [日本語](BE-XXXX-cli-bringup-consolidation-ja.md)

# BE-XXXX — Consolidate the duplicated CLI command bring-up and add a neutral DeviceError

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-cli-bringup-consolidation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`run`, `crawl`, `record`, and `audit` each bring up the same four pieces of device/backend
plumbing before doing their own work — actuator selection, the target's launch server, udid
resolution, and (where applicable) the alert guard — and each command re-implements the same
`try: … except RuntimeError → typer.Exit(2)` (or `except DeviceError`) boilerplate around them.
This item moves each pair of copies onto one shared helper in `bajutsu/cli/_shared.py`, and gives
`adb.DeviceError` a platform-neutral base so generic handlers stop importing the iOS `simctl`
module just to name the exception they catch.

## Motivation

Four copies of the same bring-up logic, each hand-written per command:

- **Actuator selection** — `try: ensure_web_runtime(...); actuator = select_actuator(...) except
  RuntimeError → typer.Exit(2)` at `run.py:303-312`, `crawl.py:501-507`, `record.py:196-202`, and
  `audit.py:146-151`. Each copy provisions the web runtime, resolves the actuator, and exits 2 on
  the same `RuntimeError`, with only the surrounding call sites differing.
- **Launch-server bring-up** — `try: start_launch_server(eff, upload_exec=...) except RuntimeError
  → typer.Exit(2)` at `run.py:504-510`, `crawl.py:286-292`, `record.py:216-220`, and
  `audit.py:163-170`. Same call, same exception, same exit code, four times.
- **udid resolution** — `_simctl.resolve_udid(...)` wrapped in `except _simctl.DeviceError →
  typer.Exit(2)` at `run.py:515`, `record.py:211-212`, `audit.py:152-156`, and `crawl.py:322`.
- **Alert-guard construction** — `ClaudeAlertLocator(ai=eff.ai, redactor=redactor)` followed by
  `SystemAlertGuard(locator, instruction).dismiss` at `run.py:375` and `392`, `crawl.py:231-232`,
  and `record.py:207-208`. Note these three are *not* byte-identical: `run.py`'s
  `_alert_guard_factory` (`run.py:363-376`) first runs a `credential_gap(eff.ai)` check and, on a
  missing credential, skips constructing the locator (falls back to `None` with a user-facing
  warning), whereas `crawl.py`/`record.py` construct it unconditionally. So the shared helper is not
  a pure behavior-preserving lift of all three — see Detailed design item 3 for how this is resolved.

A behavior change to any one of these — a different exit code, an extra log line before the
exit, a new exception to catch — has to be applied to every copy by hand, and a missed copy is a
silent inconsistency between commands rather than a test failure, since each copy passes its own
command's tests independently.

Separately, `adb.DeviceError` subclasses `simctl.DeviceError` (`bajutsu/adb.py:32`), so the 10
call sites that only want to catch *some* device error — not an iOS-specific one — still import
`bajutsu.simctl` to name the exception: `crawl.py:1016`, `cli/commands/crawl.py:189,322,365`,
`cli/commands/run.py:516`, `cli/commands/audit.py:158,193`, `cli/commands/record.py:242`,
`cli/commands/doctor.py:181`, and `serve/operations/doctor.py:130`. That inverts the dependency the prime
directive expects: `bajutsu` is meant to be backend-agnostic (platform is a backend behind one
interface), yet a purely generic `except DeviceError` handler currently can't be written without
reaching into the iOS backend's module.

## Detailed design

MECE by the two independent problems named above — the duplicated bring-up, and the exception
hierarchy inversion — with the first split into the four copied pieces:

1. **`_select_actuator_or_exit(backend, eff, engines)`** in `bajutsu/cli/_shared.py` — folds the
   `ensure_web_runtime` loop, `select_actuator` call, and the `except RuntimeError → typer.Exit(2)`
   boundary into one helper returning `(actuator, backends)`, replacing the four call-site copies
   in `run.py`, `crawl.py`, `record.py`, and `audit.py`.
2. **`_start_launch_server_or_exit(eff, *, upload_exec)`** in `bajutsu/cli/_shared.py` — folds the
   `start_launch_server` call and its `except RuntimeError → typer.Exit(2)` boundary into one
   helper returning `(stop_server, exec_decision)`, replacing the four copies. Call-site
   differences (e.g. `crawl`'s `atexit.register(stop_server)` vs. `run`'s `finally: stop_server()`)
   stay at the call site — the helper only owns the bring-up-and-exit part all four share. One
   call-site difference needs an explicit decision: `audit.py`'s `except RuntimeError` block
   (`audit.py:171-173`) also calls `shutdown()` to tear down the already-created device-pool lease
   before `typer.Exit(2)`, which the other three don't. The helper takes an optional
   `on_error: Callable[[], None] | None` cleanup hook (run before it exits) so `audit` passes
   `shutdown` and keeps that teardown; the other three pass nothing and behave exactly as today.
3. **`_build_alert_guard(eff, redactor, instruction)`** in `bajutsu/cli/_shared.py` — folds the
   `ClaudeAlertLocator` + `SystemAlertGuard(...).dismiss` construction into one helper returning
   the bound `dismiss` callable, replacing the three copies in `run.py`, `crawl.py`, and
   `record.py`. The helper absorbs `run.py`'s `credential_gap(eff.ai)` branch
   (`run.py:363-376`): when the credential is missing it emits the same warning and returns a
   no-op guard. This is *not* purely behavior-preserving for two of the three sites — `crawl.py`
   and `record.py` construct the locator unconditionally today, so folding them in means they
   gain `run`'s graceful no-op-on-missing-credential behavior instead of proceeding without it.
   That alignment is deliberate (all three AI-authoring commands should degrade the same way), and
   is called out here so it is a decided behavior change, not an accidental one.
4. **udid resolution stays a thin per-call-site wrapper**, not a fourth shared helper: each call
   site's `_simctl.resolve_udid(...)` already differs in its non-udid arguments, so the only
   shared part is the `except DeviceError → typer.Exit(2)` boundary — which collapses into a
   one-line `except device_errors.DeviceError` once step 5 lands, with no separate helper needed.
5. **`bajutsu/device_errors.py`** — a new module defining a platform-neutral `DeviceError`
   (message-carrying, matching the existing `simctl.DeviceError` shape). `simctl.DeviceError` and
   `adb.DeviceError` both become subclasses of it (each keeps its own class for platform-specific
   detail; neither subclasses the other any more). The 10 generic `except _simctl.DeviceError` /
   `except simctl.DeviceError` call sites listed under Motivation switch to
   `except device_errors.DeviceError`, dropping their `bajutsu.simctl` import; only call sites that
   genuinely need an iOS-specific `simctl.DeviceError` (if any) keep importing it directly.

Every new helper in `_shared.py` is behavior-preserving: same exception caught, same message
echoed, same `typer.Exit(2)`, same return value shape as the code it replaces — this item moves
the boilerplate, it does not change the exit-code contract. `_shared.py`'s own module docstring
already scopes it to "the genuinely cross-command pieces"; these four qualify because all four
already exist unchanged (module for module) in every command file.

## Alternatives considered

- **A decorator wrapping each command's entry point.** Considered and rejected: the four pieces
  bring up at different points inside each command (actuator selection before config resolution
  finishes, the launch server after the plan is built, the alert guard only where
  `--dismiss-alerts` applies), so a single decorator would need parameters mirroring the plain
  helpers anyway, while making the Typer command signatures — and the exact point each exit can
  fire — harder to read at the call site than an explicit function call.
- **Leave `adb.DeviceError` subclassing `simctl.DeviceError`.** Rejected: it is the direct cause of
  the `bajutsu.simctl` import at every generic handler, and a third platform backend (Android is
  already implemented; more may follow) would otherwise need to either subclass `simctl.DeviceError`
  too (compounding the inversion) or introduce its own ad hoc base.
- **Move the four helpers into `bajutsu/orchestrator.py` or a driver module instead of
  `cli/_shared.py`.** Rejected: all four are CLI-command bring-up concerns (config, Typer exit
  codes, `typer.echo`) with no role in the deterministic driver/orchestrator core — `_shared.py` is
  where the existing per-command-duplicated helpers (e.g. config loading, secret redaction) already
  live for exactly this reason.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `_select_actuator_or_exit` in `cli/_shared.py`; `run`, `crawl`, `record`, `audit` migrated
- [ ] `_start_launch_server_or_exit` in `cli/_shared.py`; `run`, `crawl`, `record`, `audit` migrated
- [ ] `_build_alert_guard` in `cli/_shared.py`; `run`, `crawl`, `record` migrated
- [ ] `bajutsu/device_errors.py` with a neutral `DeviceError`; `simctl.DeviceError` and
      `adb.DeviceError` rebased onto it; generic call sites switched off `bajutsu.simctl`

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
