**English** · [日本語](BE-XXXX-totp-window-alignment-ja.md)

# BE-XXXX — Wait for a TOTP window with enough time left

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-totp-window-alignment.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

This item adds an optional `minRemaining` field to the `totp` step ([BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)).
When the current 30-second window has fewer than `minRemaining` seconds left, the step waits for the
next window to start and only then generates the code. A code produced this way stays valid long
enough for the steps that consume it, so a sign-in no longer fails when the window closes between
`totp` and `type`.

## Motivation

A time-based one-time password (TOTP) code is valid for one 30-second window of the clock. Today
the `totp` step computes the code for the current instant and ignores how much of the window is left
([`handlers/totp.py:23`](../../bajutsu/common/orchestrator/actions/handlers/totp.py)). The scenario then
spends time on the steps that carry the code to the app: a `type` into the code field, a `tap` on
the submit button, and the app's own round trip to the server.

That time is small but not zero. A run whose `totp` step lands in the last seconds of a window
submits a code that the app rejects as expired. The same scenario passes on the next attempt,
because the next attempt starts at a different point of the window. The failure is therefore
intermittent, and it cannot be reproduced on demand.

An author cannot fix this with the tools that exist today. The `sleep` step pauses for a fixed
number of seconds, capped at 30, and its length cannot depend on the clock. The window boundary
moves with every run, so no fixed pause aligns to it.

The verifiable outcome is a scenario that carries `totp: { …, minRemaining: 10 }` ahead of the code
entry. Across runs started at any point of the window, the code is generated with at least ten
seconds of validity left, which covers the steps that carry it to the app. A sign-in that previously failed at the window edge passes on its first
attempt. The step's wait is also bounded: it never exceeds one window plus one cancellation slice.

## Detailed design

### Scenario surface

`Totp` ([`models/actions/totp.py`](../../bajutsu/common/scenario/models/actions/totp.py)) gains one
optional field. The spelling follows the camelCase aliases of the neighbouring steps.

```yaml
- totp: { secret: "${secrets.TOTP_SEED}", into: { var: code }, minRemaining: 10 }
- type: { text: "${vars.code}", into: { id: auth.code } }
- tap: { id: auth.submit }
```

| Field | Type | Meaning |
|---|---|---|
| `minRemaining` | integer, `1 ≤ n ≤ 30`, strict | The smallest number of seconds the window must still have when the code is generated. Omitted means no wait, which is today's behavior. |

The model rejects a value outside the range at load time, like `sleep` rejects a pause above its cap.
The upper bound is the window length, which is fixed at 30 seconds because the step does not expose
`period`. The value 30 is meaningful: it makes the step always wait for the next window boundary,
unless the instant is exactly on a boundary.

### Wait semantics

The step computes the remaining time from the wall clock, because a TOTP window is defined on Unix
time.

```
remaining = 30 - (now % 30)
if remaining < minRemaining: wait until the next window starts
```

After the wait the step generates the code with the same function as before (`totp()` in
[`common/totp.py`](../../bajutsu/common/totp.py)). The code is for the window that has just begun, so
it has at least `30 - ε` seconds of validity, where `ε` is the small delay of the wake-up.

The wait is a condition wait whose condition is "the window has started". Its duration is computed
from the clock, not chosen by the author, and it never exceeds one window plus one
cancellation slice. For this reason it does
not appear in the determinism audit as a `fixed-sleep` finding. The audit keeps its current rule
set, and it gains no informational finding for the wait either. The audit lists a pause because the
author can replace it with a condition wait. This wait has no such replacement, because the TOTP
window imposes it, so a finding would carry no action. The time the wait costs stays visible in two
places: the step label in the live log, and the step's duration in the report.

The wait function computes the next boundary once, `target = (floor(now / 30) + 1) * 30`. It then
sleeps until `wall() >= target`, re-reading the wall clock after each slice. The wait ends on the
boundary itself, not on `remaining >= minRemaining`, so `minRemaining: 30` terminates. Just past a
boundary the remaining time is `30 - δ`, which never reaches 30.

Re-reading the wall clock guards against a monotonic sleep that returns slightly before the wall
clock crosses the boundary. A monotonic deadline also ends the wait. The function sets it
once, to `clock.now() + (target - now) + _SLEEP_SLICE`. A wall-clock jump such as a Network Time
Protocol (NTP) correction therefore cannot stretch the wait past one window plus one slice.

A large wall-clock jump can still leave less than `minRemaining` after the wait. A backward jump lets
the monotonic deadline fire before the wall clock reaches `target`. A forward jump can land in the
tail of a later window. After the wait, the function compares how far the wall clock moved with how
far the monotonic clock moved. When the two differ by more than one slice, the wall clock jumped.
The step then still generates the code and continues, and it records a warning with its outcome
that states both measurements, for example `totp: wall clock jumped 12s during window wait; 4s left`.
The report then shows the cause next to a sign-in that later fails on an expired code.

A late wake-up, on a loaded host for example, moves both clocks together and records
no warning. The code is still for the window that has just begun, and the delay costs no more than its own
length. Under `minRemaining: 30`, a warning keyed on the remaining time alone would appear on every late
wake-up and name a clock jump that never happened.

The warning travels in a new evidence-only field, `StepOutcome.warnings: tuple[str, ...]`
([`step_outcome.py`](../../bajutsu/common/orchestrator/types/step_outcome.py)), whose default is
empty. The field leaves `ok` and `reason` untouched, so a passing step never carries failure text.
The field is plural so that a later item can add its own non-fatal warning without displacing this
one. The manifest records the field, and the HTML report renders it on the step.

### Where the wait runs

The run loop, not the action handler, owns the wait. The handler signature carries neither the
`Clock` nor the cancel source, which is why `sleep` and `email` are dispatched inside
[`_run_step_body`](../../bajutsu/common/orchestrator/loop/_functions.py) (`_do_sleep` at line 373).
The `totp` wait follows the same placement:

- Before the handler runs, the loop checks `step.totp.minRemaining`. When it is set, the loop calls a
  new `_wait_for_totp_window(min_remaining, clock, cancelled, wall) -> str | None`. The function
  returns the warning text, or `None` when no jump occurred. The loop skips the wait when
  `bindings` is `None`, matching the handler's own early return.
- The function sleeps in `_SLEEP_SLICE` slices through `clock.sleep`, and checks `cancelled()` before
  each slice. On cancellation it raises `RunCancelled`, as `_do_sleep` does, rather than returning. A cancelled run therefore leaves within one slice, as it does for `sleep` (BE-0370).
- `_run_step_body` returns a tuple with no slot for a warning. The loop appends the returned text to a new out-parameter, `warnings: list[str] | None`, in the style of the existing
  `alerts` and `system_alert_taps` parameters. The step runner passes the list in and copies it onto
  `StepOutcome.warnings`. Neither the return type nor its call sites change.
- `wall` is a `Callable[[], float]` that defaults to `time.time`. A test injects a fake, so the
  boundary arithmetic is exercised without waiting 30 real seconds. `RealClock.now()` is monotonic
  ([`real_clock.py`](../../bajutsu/common/orchestrator/types/real_clock.py)), so it cannot supply the
  window phase. The `Clock` still supplies the sleeping.
- The handler stays unchanged and computes the code from `time.time()` after the wait.

`_step_label` in [`actions/_registry.py`](../../bajutsu/common/orchestrator/actions/_registry.py)
shows the requirement, so the live log explains a visible pause. The requirement follows the step's
own `name` when it has one, as the `sleep` branch already does. An unnamed step shows
`totp (≥10s left)`, and a named one shows `Sign in code (totp ≥10s left)`.

### Prime directives

- **No LLM on the run path.** The change is clock arithmetic. Pass or fail still comes only from
  assertions.
- **Determinism.** The wait ends on a condition that the clock decides, with an upper bound of one
  window plus one cancellation slice. No unbounded poll and no author-chosen fixed pause is introduced.
- **App-agnostic.** The field is per step. No target or app configuration is involved.

### Compatibility

The default is "no wait", so no existing scenario changes behavior or timing. The field is optional,
so a scenario that never sets it dumps identically. Evidence snapshots (`redact_totp_secrets`) mask
only `secret` and are unaffected. Codegen already emits a `// TODO` for `totp`
([`codegen/xcuitest.py:365`](../../bajutsu/codegen/xcuitest.py)) and needs no change.

### Out of scope

- Exposing `period` and `digits` on the step. The generator accepts both, but the step fixes 30
  seconds and six digits. A separate item can widen the step, and then the upper bound of
  `minRemaining` becomes `period`.
- A default `minRemaining`. A non-zero default would lengthen every existing run by up to that many
  seconds.
- Changing the `email` step, which has no window to cross.

### Work breakdown

1. **Model and validation.** Add `min_remaining` (alias `minRemaining`) to `Totp`, with the range
   check and a clear error message, plus the JSON schema if one is generated from the models.
2. **Wait function.** Add `_wait_for_totp_window` next to `_do_sleep`, with the injectable wall clock.
   Its tests cover `minRemaining: 30`, a late wake-up, a wall clock that jumps backward or forward
   during the wait, and cancellation. Both jumps end the wait within the bound and produce the
   warning. `minRemaining: 30` and the late wake-up end without a warning. Cancellation raises
   `RunCancelled`.
3. **Loop wiring and label.** Call the wait from `_run_step_body` before the handler, and extend
   `_step_label` in `actions/_registry.py`.
4. **Warning on the step outcome.** Add `StepOutcome.warnings` and the `warnings` out-parameter of
   `_run_step_body`, and copy the list onto the outcome in the step runner. Record the field in the
   manifest and bump `SCHEMA_VERSION` (15 today, in `bajutsu/common/report/manifest.py`) with a note
   that a step without `warnings` on an older run means the run predates the field. Render the field
   in the HTML report. A test checks that a passing step with a warning keeps `ok` true and an empty
   `reason`.
5. **Documentation.** Update [`docs/scenarios.md`](../../docs/scenarios.md), [`docs/dsl-grammar.md`](../../docs/dsl-grammar.md),
   and the `docs/ja/` mirrors, and add a short note to [`docs/architecture.md`](../../docs/architecture.md)
   where the `totp` step is described.

## Alternatives considered

| Option | Why it was not chosen |
|---|---|
| A boolean `alignToWindow` that always waits for the next boundary | It costs 15 seconds on average and 30 at worst on every run. `minRemaining: 30` expresses it for authors who want it, so the numeric field covers this case. |
| A separate step such as `waitForTotpWindow` | It splits one intent into two steps, and a step inserted between them makes the wait meaningless. The generation and the guarantee belong in one step. |
| The existing `sleep` step | The pause must depend on the clock and the boundary moves each run. `sleep` has a fixed length and a 30-second cap, and the audit would report it as `fixed-sleep`. |
| Retry the sign-in when the app rejects the code | The retry hides a real failure behind a second attempt. Many apps count failed codes toward a lockout, so the retry can break the account the run depends on. |
| Fail the step when a clock jump leaves less than `minRemaining` after the wait | A rare wall-clock correction would then fail an otherwise healthy run. The code may still be accepted, and a warning with the outcome keeps the cause visible without stopping the run. |
| A non-zero default for `minRemaining` | It changes the timing of every existing scenario and hides the reason for the wait from a reader of the YAML. An explicit field keeps the cause visible. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Model and validation: `minRemaining` on `Totp`
- [ ] Wait function: `_wait_for_totp_window` with an injectable wall clock
- [ ] Loop wiring and step label
- [ ] Warning on the step outcome: `StepOutcome.warnings`, the manifest, and the HTML report
- [ ] Documentation: scenarios, DSL grammar, architecture, and the Japanese mirrors

## References

- [BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) — introduced the `totp` step.
- [BE-0152](../BE-0152-totp-seed-artifact-leak/BE-0152-totp-seed-artifact-leak.md) — keeps literal seeds out of run artifacts.
- RFC 6238, "TOTP: Time-Based One-Time Password Algorithm".
