**English** · [日本語](BE-XXXX-xcuitest-cold-spawn-resilience-ja.md)

# BE-XXXX — Make the XCUITest cold runner spawn diagnosable and self-healing

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-cold-spawn-resilience.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md) |
<!-- /BE-METADATA -->

## Introduction

When the on-device XCUITest runner fails to come up from a cold `xcodebuild test-without-building`,
the driver polls `GET /health` for the full 120-second startup budget and then fails with `health
never ready` — carrying no evidence of why. The runner's `xcodebuild` output is discarded to
`/dev/null`, and the wait never checks whether that process has already exited, so a launch that
died at second three still burns the remaining 117 seconds against a port nothing is listening on.
This item makes the cold spawn **diagnosable** (capture the runner's output and surface it on
failure), **fail fast** (abort the moment the `xcodebuild` process dies rather than polling a dead
port for two minutes), and **self-healing** against a one-off cold-start blip (retry the spawn once
before failing loudly) — without weakening the verdict: a genuinely broken build fails both
attempts and still stops the gate.

## Motivation

The `conformance (xcuitest)` job flakes on CI. One run failed with 14 errors, every one of them
the same setup failure:

```
XcuitestChannelError: xcuitest runner did not come up within 120.0s (health never ready)
```

The module-scoped fixture that launches the runner failed once, and all 14 conformance tests then
errored at setup, sinking the whole (required) job. The captured log carries a single repeated
line for the full budget:

```
runner channel GET /health failed (attempt 1/3), retrying: [Errno 61] Connection refused
runner channel GET /health failed (attempt 2/3), retrying: [Errno 61] Connection refused
… (the pair repeats for ~120s)
```

`Connection refused` throughout — not a socket timeout — means the runner's loopback server never
opened its port: the XCTest host launched by `xcodebuild test-without-building` did not reach the
point of binding the socket. Sibling xcuitest lanes on the same commit built and ran, so this is
not a code regression; it is a cold-start blip of the XCTest host inside a loaded CI Simulator.

Two properties of the current cold spawn (`XcuitestEnvironment._spawn_cold` in
`bajutsu/platform_lifecycle/environments/xcuitest.py`) turn that blip into an undiagnosable,
maximally slow failure:

1. **The runner's output is a black hole.** The spawn runs
   `subprocess.Popen(["xcodebuild", "test-without-building", …], stdout=subprocess.DEVNULL,
   stderr=subprocess.DEVNULL)`. When the runner never answers, there is no trace to tell a
   code-signing failure from an app crash from a wedged Simulator — the one artifact that would
   explain the failure is thrown away.
2. **The wait ignores a dead process.** The cold spawn blocks in `driver.await_ready`, which polls
   `/health` for the whole 120 seconds; nothing interleaves a check of the `xcodebuild` handle
   (`self._runner_proc`) that the environment — not the driver — holds. Even when `xcodebuild`
   exits early, the wait keeps probing a port nothing owns until the budget elapses, so the
   fastest-to-diagnose failure (the process is already gone) becomes the slowest.

The result mirrors the flaky-gate cost that
[BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
and [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
set out to remove: a red required check that carries no signal about whether anything is actually
wrong, cleared only by re-running the job by hand.

Those items, and
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md),
all harden the channel **after** the runner is up — transient-transport retry, mid-run crash
recovery, and app-readiness once the app has launched. None touches the **cold spawn of the runner
process itself**, which is the failure mode here. This item fills that gap.

## Detailed design

The work breaks into five independent units.

1. **Capture the runner's `xcodebuild` output.** Redirect the `test-without-building` subprocess's
   stdout and stderr to a per-spawn log file under the run's temporary/evidence area instead of
   `DEVNULL`. The file is the primary diagnostic and is retained on a failed spawn. On success it
   costs one small file that teardown can prune.

2. **Surface the captured output on startup failure.** When the startup wait fails — whether by
   timeout or by the dead-process check of unit 3 — read a bounded tail of the captured log and
   include it in the `XcuitestChannelError` message. The CI log then shows *why* the runner never
   answered, not merely that it did not. Quoting the tail verbatim keeps the path deterministic,
   with no LLM involved (prime directive 1).

3. **Fail fast on a dead runner process.** Today the cold spawn waits by calling
   `driver.await_ready`, a thin wrapper over the shared `_await_health` poll. Route the cold-spawn
   wait instead through the liveness-aware helper of unit 5: between health probes it checks the
   `xcodebuild` handle (`self._runner_proc.poll()`) that the environment owns, and the moment that
   returns a non-`None` exit code it aborts immediately with a distinct diagnostic (the exit code
   plus the unit-2 tail) rather than polling `Connection refused` for the remaining budget. Leave
   `_await_health` itself unchanged, so the crash-recovery path (`bajutsu/drivers/xcuitest.py`)
   that also calls it is untouched: it drives a resident runner over the channel and holds no local
   subprocess to poll, so the mid-run BE-0287 behavior is preserved.

4. **Retry the cold spawn once.** A cold XCTest-host launch that never opens its port is a
   transient infrastructure blip — the same class BE-0207 absorbs at the transport layer. On the
   first cold-spawn failure (from unit 3's fail-fast or the timeout), discard the dead runner,
   re-run the spawn, and await once more; on the **second** failure, fail loudly with both
   attempts' captured tails. A single retry absorbs a one-off cold-start blip without masking a
   repeatable failure: a broken build, signature, or app crashes both attempts and still stops the
   gate,
   preserving the "flakiness is never tolerated by absorption" stance of
   [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md). The
   The single retry applies only to the cold spawn, never to the warm-reuse path (BE-0291).

5. **Off-device tests over the spawn/wait seam.** Factor the "await readiness with a liveness check
   and a bounded retry" logic into a helper that takes an injectable process handle and spawn
   thunk, so it is exercisable without a Simulator (the same isolation the channel tests already
   use by injecting a fake transport). Cover: a process whose `poll()` returns non-`None` makes the
   wait fail fast (unit 3) and its message carries the captured tail (unit 2); a first-attempt
   failure followed by a second-attempt success exercises the single retry (unit 4); a repeatable
   failure fails loudly after exactly two attempts and no more.

## Alternatives considered

- **Raise the 120-second startup timeout.** The port is refused for the entire window, so a longer
  wait only fails more slowly — it treats a symptom (too little patience) that is not the cause
  (the runner never binds). Rejected.
- **A job-level rerun (`pytest-rerunfailures` or a GitHub re-run).** A rerun hides the flake rather
  than surfacing its cause, and re-runs the whole 14-test module — including another 120-second
  dead-wait — to recover from one bad spawn. The fail-fast plus single cold-spawn retry is cheaper
  and keeps the diagnostic. A manual job rerun stays a complementary operational fallback, not the
  fix.
- **An unbounded spawn retry.** Retrying without a bound would absorb a real, repeatable failure
  and mask a broken build, exactly the absorption BE-0049 rejects. Bounded to a single retry.
- **Stream `xcodebuild` output live to the CI log** instead of a file. Live streaming interleaves
  with pytest's captured output and is noisy on the success path; a captured file, tailed only on
  failure, gives the same diagnostic while staying quiet when the runner comes up.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — capture the runner's `xcodebuild` output to a per-spawn log file.
- [ ] Unit 2 — surface a bounded tail of the captured log in the startup-failure error.
- [ ] Unit 3 — fail fast when the `xcodebuild` process exits during the cold-spawn wait.
- [ ] Unit 4 — retry the cold spawn once before failing loudly.
- [ ] Unit 5 — off-device tests over the spawn/wait seam.

## References

- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
- [BE-0218 — Stabilize the E2E Simulator gate](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
- [BE-0290 — Make XCUITest the default iOS backend and retire idb](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)
- [BE-0049 — Determinism and flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
- `bajutsu/platform_lifecycle/environments/xcuitest.py` — `_spawn_cold`, `_discard_runner` (the cold spawn and its teardown).
- `bajutsu/drivers/xcuitest.py` — `await_ready`, `_await_health`, `_with_retry` (the startup wait and the channel retry seam).
