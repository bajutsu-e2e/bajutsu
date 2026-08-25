# Known CI failure patterns

The symptom-to-classification table this repository's CI failures are matched against. Each entry
names what the failure looks like from the outside, where to confirm it, and what the confirmation
means. Consulted by [`investigate-ci-failure`](../SKILL.md) steps 2 and 3.

**Append to this file whenever a new pattern is confirmed.** The whole point is that a symptom
diagnosed once does not have to be re-derived from raw artifacts the next time. An entry earns its
place by having been observed and confirmed — never by being plausible.

## `gate-mechanical` — the `ci.yml` `check` job

Each of these fails a specific gate step, is unambiguous from the step name alone, and has one
fix command. No artifact download is needed: `gh run view <run-id> --log-failed` names the step.

| Failing step | Symptom in the log | Fix |
|---|---|---|
| `make lock-check` | `uv lock --check` reports the lockfile is out of date with `pyproject.toml` | `uv lock` |
| `make format-check` | `ruff format --check` lists files it would reformat | `make format` |
| `make lint-skills` | `apm audit --ci` reports drift between `.apm/skills/` and `.claude/skills/` | `make skills` |

The `lint-skills` case has one trap worth stating: the drift is usually a **forgotten `make skills`**
after editing a skill source, but it is equally produced by hand-editing the deployed
`.claude/skills/` tree, which nothing should ever do. Check which side moved before running the fix,
because `make skills` resolves the first case and silently discards the second.

Every other step of the gate — `make lint`, `make typecheck`, `make test`, `make lint-roadmap`,
`make lint-module-map`, `make lint-imports`, `make lint-docstrings`, `make lint-secrets` — points at
something in the change itself. Those are `code-defect`, not mechanical.

## `e2e-known-flake` — the on-device lanes

These are host and device faults, not regressions in the change under test. A match here means the
right response is a re-run, not a code fix.

Confirm each one from the failing job's own uploaded artifact — `ios-e2e-<job>-run` or
`android-e2e-<job>-run`, whose `path: runs/` carries `runs/diagnostics/` with it. The three
diagnostic layers ([BE-0361](../../../../roadmaps/BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md),
[BE-0367](../../../../roadmaps/BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md))
land at:

- `runs/diagnostics/stalls/stall-NN-<reason>-<pid>/` — an in-process capture taken at the moment a
  stall was first observed. `probe.txt` summarises each probe's exit and elapsed time; a probe the
  capture's deadline cut off says so there, which is not the same as a probe that found nothing.
- `runs/diagnostics/host-telemetry.log` — the background sampler, every ~20s for the whole job.
- `runs/diagnostics/ps-baseline.txt` — the iOS lane's pre-run snapshot, for comparison against a
  stall capture's `ps.txt`.
- `runs/runner-logs/result-<udid>-<port>.xcresult` — iOS only: what testmanagerd itself recorded.

The `<reason>` in a stall directory is the trigger, and there are exactly four:
`runner-crash` and `video-no-bytes` (iOS), `resident-read` and `screenrecord-no-growth` (Android).

### iOS (`ios-e2e.yml`)

| # | Symptom | Confirm with | Notes |
|---|---|---|---|
| 1 | `Timed out while requesting screenshot` in the XCTest failure, or its siblings `cannot request screenshot data because it has an empty frame` / `Lost connection to the application`. `xcodebuild` exits 65; the Python side sees `GET /screenshot` time out, then `Connection refused` | `runs/runner-logs/*.xcresult`, and a `stall-NN-runner-crash-*` capture | The signature BE-0361 was written for. Nothing crashed, so a crash-report sweep finds nothing |
| 2 | `SimRenderServer` crashing on its own dispatch queue | crash reports in the diagnostics sweep | Host renderer fault. One of the four collapses that got the two-device `pool (xcuitest)` job withdrawn |
| 3 | A wedged CoreSimulator — `simctl uninstall` (or another `simctl` call) times out | `stall-NN-*` probe durations in `probe.txt`; `conformance-recovery-report` when the `conformance` job is the one failing | Named a host fault and deliberately **not** retried: rebuilding the device is made of the very `simctl` calls that just stalled ([BE-0378](../../../../roadmaps/BE-0378-ondevice-wedge-timeout-not-a-verdict/BE-0378-ondevice-wedge-timeout-not-a-verdict.md)) |
| 4 | The runner channel becomes unreachable mid-run — `base.BackendCrashError` | the job log, plus `stall-NN-runner-crash-*` | Crash recovery re-leases a device and retries within its budget; a failure means the budget was exhausted |

### Android (`android-e2e.yml`)

| # | Symptom | Confirm with | Notes |
|---|---|---|---|
| 5 | The resident UI Automator server stops answering, and the driver degrades to the `uiautomator dump` subprocess | a `stall-NN-resident-read-*` capture | The trigger fires only when the channel is gone for the rest of the lease, not on momentary noise |
| 6 | The emulator's own renderer wedges — `screenrecord` runs but produces no bytes | a `stall-NN-screenrecord-no-growth-*` capture, plus `dumpsys SurfaceFlinger --latency` in it | This lane's known flake. Distinguishes a wedged renderer from a recording that never started |
| 7 | The emulator never comes up at all | the `reactivecircus/android-emulator-runner` step's own log | The single failure in `fault-injection (adb)`'s 73-run promotion window, and nothing the lane asserts |
| 8 | The job runs out its `timeout-minutes` with no scenario verdict | `host-telemetry.log`, which covers the whole job including a died emulator step | The composite action brackets the emulator step from outside, so this layer survives a job whose emulator step died |

## What is deliberately not here

**Pixel visual-regression drift** (the `visual` jobs) and **element-tree golden drift** (the `golden`
jobs) are neither flakes nor regressions in the ordinary sense. A pixel baseline is host-specific and
a golden can drift with an upstream on-device dependency, which is exactly why neither job feeds a
required aggregator ([docs/ci.md](../../../../docs/ci.md), "Which E2E checks gate a merge"). A red
`visual` or `golden` is a signal to re-record or to investigate the upstream, and a human decides
which. Report it as `e2e-unclassified` and say the job is a non-gating signal, rather than
recommending a re-run that would change nothing.
