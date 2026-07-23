**English** · [日本語](BE-0319-xcuitest-cold-spawn-resilience-ja.md)

# BE-0319 — Make the XCUITest cold runner spawn diagnosable and self-healing

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0319](BE-0319-xcuitest-cold-spawn-resilience.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0319") |
| Topic | Platform support |
| Related | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md) |
<!-- /BE-METADATA -->

## Introduction

When the on-device XCUITest runner fails to come up from a cold `xcodebuild test-without-building`,
the driver polls `GET /health` for the full 120-second startup budget and then fails with `health
never ready` — and, by default, carries no evidence of why. PR
[#1299](https://github.com/bajutsu-e2e/bajutsu/pull/1299) laid the groundwork: an **opt-in**
capture of the runner's `xcodebuild` output (`BAJUTSU_XCUITEST_RUNNER_LOG`) whose tail rides a
mid-run-crash *warning*. This item finishes that work and makes the cold spawn **diagnosable** by
default (capture on by default for the cold-spawn path, and the tail folded into the failing
`XcuitestChannelError` rather than a separate warning), **fail fast** (abort the moment the
`xcodebuild` process dies rather than polling a dead port for the remaining two minutes), and
**self-healing** against a one-off cold-start blip (retry the spawn once before failing loudly) —
without weakening the verdict: a genuinely broken build fails both attempts and still stops the
gate.

## Motivation

The `conformance (xcuitest)` job flakes on CI. One run failed with 14 errors, every one of them
the same setup failure:

```
XcuitestChannelError: xcuitest runner did not come up within 120.0s (health never ready)
```

The module-scoped fixture that launches the runner failed once, and all 14 conformance tests then
errored at setup, sinking the whole (required) job. The captured log carries a pair of lines for
the full budget:

```
runner channel GET /health failed (attempt 1/3), retrying: [Errno 61] Connection refused
runner channel GET /health failed (attempt 2/3), retrying: [Errno 61] Connection refused
… (the pair repeats for ~120s)
```

`Connection refused` throughout — not a socket timeout — means the runner's loopback server never
opened its port: the XCTest host launched by `xcodebuild test-without-building` did not reach the
point of binding the socket. Sibling xcuitest lanes on the same commit built and ran, so this is
not a code regression; it is a cold-start blip of the XCTest host inside a loaded CI Simulator.

PR #1299 already captures the runner's output and reads a bounded tail of it
(`_open_runner_output` / `_runner_log_hint` in `XcuitestEnvironment`,
`bajutsu/platform_lifecycle/environments/xcuitest.py`). Three gaps remain that keep this exact CI
flake undiagnosable and maximally slow:

1. **The capture is opt-in and defaults to `DEVNULL`.** `_open_runner_output` captures only when
   `BAJUTSU_XCUITEST_RUNNER_LOG` names a directory; unset — as it is on CI — the runner spawns into
   `DEVNULL`, exactly as before. So the *first* flake, the one that fails the run, is captured
   nowhere; a human must set the variable and wait for the blip to recur. The diagnostic that
   matters is the one for the failure you already have.
2. **The tail rides a warning, not the failure.** `_runner_log_hint` is emitted by `_discard_runner`
   as a `warning` log line, while the run-failing `XcuitestChannelError: health never ready` carries
   none of it. The cause and the failure land in different places, so reading the failure still does
   not tell you why.
3. **The wait still ignores a dead process.** `_discard_runner` does check `self._runner_proc.poll()`
   — but only during teardown, *after* `await_ready` has already spun for the full 120 seconds.
   Nothing interleaves that check into the health-poll wait, so a runner that dies at second three
   still burns the remaining 117 seconds probing a port nothing owns; the fastest-to-diagnose
   failure (the process is already gone) stays the slowest.

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

The work breaks into five independent units. Units 1 and 2 build directly on PR #1299's capture
seam (`_open_runner_output` / `_runner_log_hint`); units 3–5 are new.

1. **Default the runner-output capture on for the cold-spawn path.** #1299 captures only when
   `BAJUTSU_XCUITEST_RUNNER_LOG` is set. Make the cold spawn capture by default — to the run's
   temporary/evidence area — so the first CI flake is diagnosable without a human pre-arming the
   variable, and keep the existing variable as an override for the capture directory. On success
   the capture costs one small file that teardown can prune.

2. **Fold the captured tail into the startup-failure error.** Today `_runner_log_hint`'s tail
   reaches only the `_discard_runner` warning. When the startup wait fails — whether by timeout or
   by the dead-process check of unit 3 — include that bounded tail in the `XcuitestChannelError`
   message itself, so the run-failing error shows *why* the runner never answered, not merely that
   it did not. Quoting the tail verbatim keeps the path deterministic, with no LLM involved (prime
   directive 1).

3. **Fail fast on a dead runner process during the wait.** #1299's `self._runner_proc.poll()` check
   lives in `_discard_runner` — teardown, reached only after `await_ready` has spun the full budget.
   Interleave the same check into the cold-spawn startup wait through a liveness-aware helper (unit
   5): between health probes it polls the `xcodebuild` handle the environment owns, and the moment
   `poll()` returns a non-`None` exit code it aborts immediately with a distinct diagnostic (the
   exit code plus the unit-2 tail) rather than polling `Connection refused` for the remaining
   budget. Leave `_await_health` itself unchanged, so the crash-recovery path
   (`bajutsu/drivers/xcuitest.py`) that also calls it is untouched: it drives a resident runner over
   the channel and holds no local subprocess to poll, so the mid-run BE-0287 behavior is preserved.

4. **Retry the cold spawn once.** A cold XCTest-host launch that never opens its port is a
   transient infrastructure blip — the same class BE-0207 absorbs at the transport layer. On the
   first cold-spawn failure (from unit 3's fail-fast or the timeout), discard the dead runner,
   re-run the spawn, and await once more; on the **second** failure, fail loudly with both
   attempts' captured tails. A single retry absorbs a one-off cold-start blip without masking a
   repeatable failure: a broken build, signature, or app crashes both attempts and still stops the
   gate,
   preserving the "flakiness is never tolerated by absorption" stance of
   [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md). The
   single retry applies only to the cold spawn, never to the warm-reuse path
   ([BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md)).

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
- **Leave the capture opt-in, as #1299 left it.** Requiring an operator to pre-set
  `BAJUTSU_XCUITEST_RUNNER_LOG` means the flake that fails the gate — the first occurrence — is
  never captured; only a later reproduction is. Defaulting the capture on for the cold-spawn path
  (unit 1) captures the failure you already have. Rejected in favor of on-by-default.
- **Stream `xcodebuild` output live to the CI log** instead of a file. Live streaming interleaves
  with pytest's captured output and is noisy on the success path; a captured file, tailed only on
  failure, gives the same diagnostic while staying quiet when the runner comes up.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — default the runner-output capture on for the cold-spawn path (builds on #1299).
- [ ] Unit 2 — fold the captured tail into the `XcuitestChannelError` startup-failure message.
- [ ] Unit 3 — fail fast when the `xcodebuild` process exits during the cold-spawn wait.
- [ ] Unit 4 — retry the cold spawn once before failing loudly.
- [ ] Unit 5 — off-device tests over the spawn/wait seam.

## References

- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
- [BE-0218 — Stabilize the E2E Simulator gate](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
- [BE-0290 — Make XCUITest the default iOS backend and retire idb](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)
- [BE-0049 — Determinism and flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
- [PR #1299](https://github.com/bajutsu-e2e/bajutsu/pull/1299) — the opt-in runner-output capture this item builds on.
- `bajutsu/platform_lifecycle/environments/xcuitest.py` — `_spawn_cold`, `_open_runner_output`, `_runner_log_hint`, `_discard_runner` (the cold spawn, its output capture, and its teardown).
- `bajutsu/drivers/xcuitest.py` — `await_ready`, `_await_health`, `_with_retry` (the startup wait and the channel retry seam).
