**English** · [日本語](BE-0319-xcuitest-cold-spawn-resilience-ja.md)

# BE-0319 — Make the XCUITest cold runner spawn diagnosable and self-healing

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0319](BE-0319-xcuitest-cold-spawn-resilience.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0319") |
| Implementing PR | [#1327](https://github.com/bajutsu-e2e/bajutsu/pull/1327), [#1403](https://github.com/bajutsu-e2e/bajutsu/pull/1403) |
| Topic | Platform support |
| Related | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md) |
<!-- /BE-METADATA -->

## Introduction

The on-device XCUITest runner can fail to come up from a cold `xcodebuild test-without-building`.
In that case, the driver polls `GET /health` for the full 120-second startup budget. The driver then
fails with `health never ready`. By default, the failure carries no evidence of why.

PR [#1299](https://github.com/bajutsu-e2e/bajutsu/pull/1299) laid the groundwork. The PR added an
**opt-in** capture of the runner's `xcodebuild` output (`BAJUTSU_XCUITEST_RUNNER_LOG`). The tail of
the capture rides a mid-run-crash *warning*.

This item finishes that work. The item makes the cold spawn:

- **Diagnosable** by default. The capture is on by default for the cold-spawn path, and the tail
  folds into the failing `XcuitestChannelError` instead of a separate warning.
- **Fail fast.** The wait aborts the moment the `xcodebuild` process dies, instead of polling a dead
  port for the remaining two minutes.
- **Self-healing** against a one-off cold-start blip. The driver retries the spawn once before
  failing loudly.

The verdict does not weaken. A genuinely broken build fails both attempts and still stops the gate.

## Motivation

The `conformance (xcuitest)` job flakes on CI. One run failed with 14 errors, and every error was the
same setup failure:

```
XcuitestChannelError: xcuitest runner did not come up within 120.0s (health never ready)
```

The module-scoped fixture that launches the runner failed once. All 14 conformance tests then
errored at setup, which sank the whole (required) job. The captured log carries a pair of lines for
the full budget:

```
runner channel GET /health failed (attempt 1/3), retrying: [Errno 61] Connection refused
runner channel GET /health failed (attempt 2/3), retrying: [Errno 61] Connection refused
… (the pair repeats for ~120s)
```

The log shows `Connection refused` throughout, not a socket timeout. The runner's loopback server
therefore never opened its port. The XCTest host that `xcodebuild test-without-building` launched did
not reach the point of binding the socket.

Sibling xcuitest lanes on the same commit built and ran. The failure is not a code regression. The
failure is a cold-start blip of the XCTest host inside a loaded CI Simulator.

PR #1299 already captures the runner's output and reads a bounded tail of the capture
(`_open_runner_output` and `_runner_log_hint` in `XcuitestEnvironment`,
`bajutsu/platform_lifecycle/environments/xcuitest.py`). Three gaps remain. The gaps keep this exact
CI flake undiagnosable and maximally slow:

1. **The capture is opt-in and defaults to `DEVNULL`.** The function `_open_runner_output` captures
   when `BAJUTSU_XCUITEST_RUNNER_LOG` names a directory. When the variable is unset, as on CI, the
   runner spawns into `DEVNULL`, exactly as before. The *first* flake, the one that fails the run,
   is captured nowhere. A human must set the variable and wait for the blip to recur. The diagnostic
   that matters is the one for the failure you already have.
2. **The tail rides a warning, not the failure.** The function `_discard_runner` emits
   `_runner_log_hint` as a `warning` log line. The run-failing
   `XcuitestChannelError: health never ready` carries none of the tail. The cause and the failure
   land in different places, so reading the failure still does not tell you why.
3. **The wait still ignores a dead process.** The function `_discard_runner` does check
   `self._runner_proc.poll()`. The check runs during teardown, *after* `await_ready` has already
   spun for the full 120 seconds. Nothing interleaves the check into the health-poll wait. A runner
   that dies at second three still burns the remaining 117 seconds probing a port that nothing owns.
   The fastest-to-diagnose failure (the process is already gone) stays the slowest.

The result mirrors the flaky-gate cost that
[BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
and [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
set out to remove: a red required check that carries no signal about whether anything is wrong. Only
a manual re-run of the job clears the check.

Those items, and
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md),
all harden the channel **after** the runner is up. They cover transient-transport retry, mid-run
crash recovery, and app readiness once the app has launched. None touches the **cold spawn of the
runner process itself**, which is the failure mode here. This item fills that gap.

## Detailed design

The work breaks into six independent units. Units 1 and 2 build directly on the capture seam of
PR #1299 (`_open_runner_output` and `_runner_log_hint`). Units 3–5 are new. Unit 6 closes a residual
that CI surfaced after units 1–5 shipped in PR #1327.

1. **Default the runner-output capture on for the cold-spawn path.** PR #1299 captures when
   `BAJUTSU_XCUITEST_RUNNER_LOG` is set. Make the cold spawn capture by default, to the run's
   temporary and evidence area. The first CI flake is then diagnosable without a human pre-arming the
   variable. Keep the existing variable as an override for the capture directory. On success, the
   capture costs one small file that teardown can prune.

2. **Fold the captured tail into the startup-failure error.** Today the tail of `_runner_log_hint`
   reaches the `_discard_runner` warning alone. When the startup wait fails, whether by timeout or by
   the dead-process check of unit 3, include the bounded tail in the `XcuitestChannelError` message
   itself. The run-failing error then shows *why* the runner never answered, not merely that the
   runner did not answer. Quoting the tail verbatim keeps the path deterministic, with no LLM
   involved (prime directive 1).

3. **Fail fast on a dead runner process during the wait.** The `self._runner_proc.poll()` check from
   PR #1299 lives in `_discard_runner`. That function is teardown, reached after `await_ready` has
   spun the full budget. Interleave the same check into the cold-spawn startup wait through a
   liveness-aware helper (unit 5).

   Between health probes, the helper polls the `xcodebuild` handle that the environment owns. The
   moment `poll()` returns a non-`None` exit code, the helper aborts immediately with a distinct
   diagnostic: the exit code plus the unit 2 tail. The helper does not poll `Connection refused` for
   the remaining budget.

   Leave `_await_health` itself unchanged. The crash-recovery path (`bajutsu/drivers/xcuitest.py`)
   also calls `_await_health`, and the path stays untouched. That path drives a resident runner over
   the channel and holds no local subprocess to poll. The mid-run BE-0287 behavior is therefore
   preserved.

4. **Retry the cold spawn once.** A cold XCTest-host launch that never opens its port is a transient
   infrastructure blip, the same class that BE-0207 absorbs at the transport layer. On the first
   cold-spawn failure (from the fail-fast of unit 3 or from the timeout), discard the dead runner,
   re-run the spawn, and await once more. On the **second** failure, fail loudly with the captured
   tails of both attempts.

   A single retry absorbs a one-off cold-start blip without masking a repeatable failure. A broken
   build, signature, or app crashes both attempts and still stops the gate. The retry preserves the
   "flakiness is never tolerated by absorption" stance of
   [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md). The
   single retry applies to the cold spawn, never to the warm-reuse path
   ([BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md)).

5. **Off-device tests over the spawn/wait seam.** Factor the "await readiness with a liveness check
   and a bounded retry" logic into a helper. The helper takes an injectable process handle and spawn
   thunk, so tests can exercise the helper without a Simulator. The channel tests already use the
   same isolation by injecting a fake transport. The tests cover three cases:
   - A process whose `poll()` returns non-`None` makes the wait fail fast (unit 3), and the message
     carries the captured tail (unit 2).
   - A first-attempt failure followed by a second-attempt success exercises the single retry
     (unit 4).
   - A repeatable failure fails loudly after exactly two attempts and no more.

6. **End the cold-spawn wait when the test run ends, not when the ceiling expires.** After units 1–5
   shipped, the `actuation (xcuitest)` and `bundled-runner (xcuitest)` lanes kept failing before any
   scenario ran. The trigger was a Simulator app-launch timeout, which units 3 and 4 cannot reach.

   `xcodebuild` reports `Failed to launch <bundle id>: Timed out attempting to launch app`, and the
   XCTest suite finishes. The `xcodebuild` process then lingers. The liveness check of unit 3 watches
   the process, not the test run, so the check keeps seeing a live handle. The wait runs to the
   ceiling.

   Two failures observed on PR [#1398](https://github.com/bajutsu-e2e/bajutsu/pull/1398) spent 225
   and 127 seconds of a 300-second ceiling after the suite had already reported failure.

   That dead time makes the residual self-sustaining. The ceiling is a budget shared across attempts
   (unit 4), so an attempt that runs to the ceiling leaves nothing for the retry. The failure that
   the retry exists to absorb is the one failure the retry can never reach. Every occurrence raised a
   lone `attempt 1/2: health never ready`.

   Read the terminal marker out of the capture that unit 1 turned on, and end the wait there. The
   launch timeout then fails in seconds instead of minutes. The budget that the wait no longer spends
   funds the retry.

## Alternatives considered

- **Raise the 120-second startup timeout.** The port is refused for the entire window, so a longer
  wait fails more slowly. The change treats a symptom (too little patience) that is not the cause
  (the runner never binds). Rejected.
- **A job-level rerun (`pytest-rerunfailures` or a GitHub re-run).** A rerun hides the flake instead
  of surfacing the cause. A rerun also repeats the whole 14-test module, including another
  120-second dead wait, to recover from one bad spawn. The fail-fast plus single cold-spawn retry is
  cheaper and keeps the diagnostic. A manual job rerun stays a complementary operational fallback,
  not the fix.
- **An unbounded spawn retry.** Retrying without a bound would absorb a real, repeatable failure and
  mask a broken build, which is the absorption BE-0049 rejects. The retry stays bounded to one.
- **Leave the capture opt-in, as #1299 left it.** An operator would have to pre-set
  `BAJUTSU_XCUITEST_RUNNER_LOG`. The flake that fails the gate, the first occurrence, would then
  never be captured. Only a later reproduction would be. Defaulting the capture on for the cold-spawn
  path (unit 1) captures the failure you already have. Rejected in favor of on-by-default.
- **Retry the app launch inside the runner, separately from the health wait.** The Swift runner could
  catch its own `XCUIApplication.launch()` timeout and relaunch without a fresh `xcodebuild`. That
  retry is cheaper than the whole-runner retry of unit 4. Two reasons favor the Python-side detection
  of unit 6 anyway:
  - A relaunch inside a runner whose XCTest automation session has already wedged reuses the very
    session that just timed out.
  - The fix would have to ship in the Swift package instead of the logic core, so every lane would
    need a rebuilt runner to pick the fix up.

  The idea is worth revisiting if the launch timeout survives unit 6.
- **Stream `xcodebuild` output live to the CI log** instead of a file. Live streaming interleaves
  with the captured output of pytest and is noisy on the success path. A captured file, tailed on
  failure, gives the same diagnostic and stays quiet when the runner comes up.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — default the runner-output capture on for the cold-spawn path (builds on #1299).
- [x] Unit 2 — fold the captured tail into the `XcuitestChannelError` startup-failure message.
- [x] Unit 3 — fail fast when the `xcodebuild` process exits during the cold-spawn wait.
- [x] Unit 4 — retry the cold spawn once before failing loudly.
- [x] Unit 5 — off-device tests over the spawn/wait seam.
- [x] Unit 6 — end the cold-spawn wait on an ended test run, so a Simulator app-launch timeout
      fails fast and leaves budget for the retry.

## References

- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
- [BE-0218 — Stabilize the E2E Simulator gate](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
- [BE-0290 — Make XCUITest the default iOS backend and retire idb](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)
- [BE-0049 — Determinism and flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
- [PR #1299](https://github.com/bajutsu-e2e/bajutsu/pull/1299): the opt-in runner-output capture that this item builds on.
- `bajutsu/platform_lifecycle/environments/xcuitest.py`: `_spawn_cold`, `_open_runner_output`, `_runner_log_hint`, and `_discard_runner` (the cold spawn, its output capture, and its teardown).
- `bajutsu/drivers/xcuitest.py`: `await_ready`, `_await_health`, and `_with_retry` (the startup wait and the channel retry seam).
