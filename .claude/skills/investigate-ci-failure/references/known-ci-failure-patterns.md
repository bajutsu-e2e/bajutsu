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

The `lint-skills` case has **three** causes, not one, and the table's fix command only settles two
of them — so check which side moved before running it.

1. A **forgotten `make skills`** after editing a skill source. The common case; `make skills` fixes
   it.
2. A **hand-edited deployed `.claude/skills/` tree**, which nothing should ever do. `make skills`
   silently discards that edit rather than adopting it, so confirm the edit is not one somebody
   meant to keep before running it.
3. A **stale cross-skill link** in a deployed file. APM rewrites relative links between skills at
   install time, so a link to a skill that did not yet exist is deployed unrewritten, and a later
   incremental install never revisits the file — its source hash has not changed. `apm audit --ci`
   replays the install from scratch, produces the rewritten link, and reports the deployed copy as
   drifted even though it matches both its source byte for byte and the lockfile's recorded hash.
   **Re-running `make skills` does not clear this one**; only deleting the deployed tree and
   redeploying does:
   ```bash
   rm -rf .claude/skills/<skill> && make skills
   ```
   Still `gate-mechanical`, but carry the delete-and-redeploy fix rather than the table's
   `make skills`. Adding two skills that link to each other, in separate commits, is what produces
   it — the shape that first surfaced it here.

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
| 9 | A pre-run render-service wedge that outlives the probe: `render-probe.txt` already reports `screenshot: STILL RUNNING … — killed (the render service is not answering)` before any scenario runs, `backboardd` itself times out answering `pgrep`/`sample` in a later `stall-NN-video-no-bytes-*` capture, and the run eventually fails an app (re)launch with `FBSOpenApplicationServiceErrorDomain` / `"is unknown to FrontBoard"` | `diagnostics/render-probe.txt` (byte count / kill note, timestamped before any scenario) plus a `video-no-bytes` `probe.txt`'s `pgrep backboardd` / `sample:backboardd` timeout | Confirmed PR #1903: the wedge predates the change under test (the pre-run probe fails before any scenario, on scenarios untouched by that PR) and the eventual FrontBoard failure is the same host stall surfacing later as an app-launch refusal, not a new fault |
| 11 | On the `app-crash (xcuitest)` job specifically (BE-0424): the wrapped `bajutsu run` of `app_crash.yaml` exits green — the deliberate `fatalError()` trigger tap reports `accepted: true` and every later step, including the one that taps the now-expected-dead app, still resolves its selector and succeeds — so `scripts/assert_app_crash_evidence.py` fails on "the scenario passed, so the fixture never crashed the app" | the job's own step log (this job's `path:` is `tmp/showcase-app-crash-runs`, not `runs/`, so BE-0361's `runs/diagnostics/` sweep never reaches its artifact upload): a `recordVideo did not report 'Recording started' … within 5.0s` line followed by `stall diagnostics: capturing the state behind 'video-no-bytes'` timestamped over the same steps that should have crashed the app | Confirmed PR #2012 (run 35090492681): rebuilding `showcase-swiftui` + the XCUITest runner from the same commit and driving the identical scenario against a freshly booted, uncontended Simulator (same iOS 26.4) reproduces the correct result every time — step 3 fails, `app_crashed: true`, the `.ips` is captured — ruling out a defect in `CrashView.swift`, `AppModel`'s `SHOWCASE_CRASH` wiring, `_classify_app_crash`, or the runner's handle-based tap. The concurrent `video-no-bytes` stall is this job's own evidence of the same degraded render/window-server pipeline #9 names; under it, XCUITest's synthetic tap can report `accepted` (event injection succeeded) without the app ever receiving it, which silently skips the crash instead of blocking the video or the next launch. This job's artifact gap (diagnostics never uploaded) means a future occurrence must be read from the log line above rather than `stall-NN-*`/`render-probe.txt` directly — worth fixing by pointing this job's `--runs-dir` sweep at (or additionally uploading) `runs/diagnostics/`. |
| 12 | Same `app-crash (xcuitest)` job, a different half of BE-0424's pipeline: the scenario fails *correctly* this time — step 3's `tap crash.alive` reports `app_crashed: true` in `manifest.json`, proving `_classify_app_crash` / `app_crash_signal()` (`XCUIApplication.state == notRunning`) worked — but `scripts/assert_app_crash_evidence.py` still fails, now on "no app-crash/ directory at …", because `xcuitest_environment.py`'s `.ips` sweep (`_collect_app_crash_reports`, bounded to `_APP_CRASH_REPORT_TIMEOUT = 5.0`s) matched zero reports | the job's own step log, same artifact gap as #11: a `recordVideo did not report 'Recording started' … within 5.0s` line and its `stall diagnostics: capturing the state behind 'video-no-bytes'` follow-up, timestamped ~10s *before* the scenario's own step 0 even starts (compare the log's UTC timestamps against `manifest.json`'s per-step `started_at`, which needs converting off its own epoch) | Confirmed PR #2012 (run 35164359245, `run_attempt` 3, job 105041168033): the manifest proves the classification worked and the trigger tap landed, ruling out a `_step_runner.py` regression; the same video/render-pipeline stall #9 and #11 already name for this host class lands at the very top of this run, evidencing host-level contention concurrent with the scenario — a plausible reason macOS's async `ReportCrash` also missed the fixed 5s poll window even though the synthetic tap this time was delivered (unlike #11's silently-skipped variant). This job's artifact gap (see #11) means, again, the log's `video-no-bytes` line is the only confirmation available; the same `--runs-dir` / `runs/diagnostics/` fix noted in #11 would let a future occurrence be confirmed from `stall-NN-*` directly instead of by timestamp arithmetic. A recurrence carrying **no** concurrent stall line is not this pattern: that would point at `_APP_CRASH_REPORT_TIMEOUT` being too tight for `ReportCrash` on any loaded runner — a budget to widen in code, not a host flake. |

### Android (`android-e2e.yml`)

| # | Symptom | Confirm with | Notes |
|---|---|---|---|
| 5 | The resident UI Automator server stops answering, and the driver degrades to the `uiautomator dump` subprocess | a `stall-NN-resident-read-*` capture | The trigger fires only when the channel is gone for the rest of the lease, not on momentary noise |
| 6 | The emulator's own renderer wedges — `screenrecord` runs but produces no bytes | a `stall-NN-screenrecord-no-growth-*` capture, plus `dumpsys SurfaceFlinger --latency` in it | This lane's known flake. Distinguishes a wedged renderer from a recording that never started |
| 7 | The emulator never comes up at all | the `reactivecircus/android-emulator-runner` step's own log | The single failure in `fault-injection (adb)`'s 73-run promotion window, and nothing the lane asserts |
| 8 | The job runs out its `timeout-minutes` with no scenario verdict | `host-telemetry.log`, which covers the whole job including a died emulator step | The composite action brackets the emulator step from outside, so this layer survives a job whose emulator step died |
| 10 | A UI value that should update right after a correctly-delivered tap never lands within the assertion's wait bound (`wait_until` times out with no other symptom) | the failing test's per-test `device.log`/logcat: a `TouchInteractionService.onInputEvent` `ACTION_DOWN`/`ACTION_UP` pair confirms the tap was delivered, immediately followed (within the wait window) by `BLASTSyncEngine: WM sent Transaction to organized, but never received commit callback. Application ANR likely to follow.`; `diagnostics/anr.log` pulls 0 files, ruling out an actual ANR | A WindowManager/SurfaceFlinger transaction-commit stall on the emulator's compositor — the same renderer-wedge fault class as #6, just surfaced through a UI-update timeout rather than a stalled `screenrecord` capture. No `stall-NN-*` capture exists for it (BE-0367's triggers don't instrument this path). Confirmed PR #2012 (`conformance (adb)`, `test_a_tap_lands_on_the_element_the_selector_named`): the PR's `adb_driver.py`/`android_environment.py` diff is pure addition (zero lines removed or modified) and none of the added code runs on the tap/`wait_until` path, ruling out a code regression |

## What is deliberately not here

**Pixel visual-regression drift** (the `visual` jobs) and **element-tree golden drift** (the `golden`
jobs) are neither flakes nor regressions in the ordinary sense. A pixel baseline is host-specific and
a golden can drift with an upstream on-device dependency, which is exactly why neither job feeds a
required aggregator ([docs/ci.md](../../../../docs/ci.md), "Which E2E checks gate a merge"). A red
`visual` or `golden` is a signal to re-record or to investigate the upstream, and a human decides
which. Report it as `e2e-unclassified` and say the job is a non-gating signal, rather than
recommending a re-run that would change nothing.
