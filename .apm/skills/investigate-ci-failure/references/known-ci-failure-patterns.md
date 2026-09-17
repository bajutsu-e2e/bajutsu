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
| 9 | A pre-run render-service wedge that outlives the probe: `render-probe.txt` already reports `screenshot: STILL RUNNING … — killed (the render service is not answering)` before any scenario runs, `backboardd` itself times out answering `pgrep`/`sample` in a later `stall-NN-video-no-bytes-*` capture, and the run eventually fails an app (re)launch with `FBSOpenApplicationServiceErrorDomain` / `"is unknown to FrontBoard"` | `diagnostics/render-probe.txt` (byte count / kill note, timestamped before any scenario) plus a `video-no-bytes` `probe.txt`'s `pgrep backboardd` / `sample:backboardd` timeout | Confirmed PR #1903: the wedge predates the change under test (the pre-run probe fails before any scenario, on scenarios untouched by that PR) and the eventual FrontBoard failure is the same host stall surfacing later as an app-launch refusal, not a new fault. Reconfirmed PR #2012 (`actuation (xcuitest)`, run 35185561508, job 105090800802): two `recordVideo did not report 'Recording started'` / `video-no-bytes` stalls plus a Simulator reboot after a failed cold runner spawn, culminating in an unrelated `wait timeout` for `horse.title` — the same host fault class as #11/#12, but surfacing through a plain `wait` on an ordinary navigation scenario rather than the crash-detection pipeline, confirming #9 is job-agnostic rather than specific to `app-crash (xcuitest)` |
| 11 | On the `app-crash (xcuitest)` job specifically (BE-0424): the wrapped `bajutsu run` of `app_crash.yaml` exits green — the deliberate `fatalError()` trigger tap reports `accepted: true` and every later step, including the one that taps the now-expected-dead app, still resolves its selector and succeeds — so `scripts/assert_app_crash_evidence.py` fails on "the scenario passed, so the fixture never crashed the app" | the job's own step log (this job's `path:` is `tmp/showcase-app-crash-runs`, not `runs/`, so BE-0361's `runs/diagnostics/` sweep never reaches its artifact upload): a `recordVideo did not report 'Recording started' … within 5.0s` line followed by `stall diagnostics: capturing the state behind 'video-no-bytes'` timestamped over the same steps that should have crashed the app | Confirmed PR #2012 (run 35090492681): rebuilding `showcase-swiftui` + the XCUITest runner from the same commit and driving the identical scenario against a freshly booted, uncontended Simulator (same iOS 26.4) reproduces the correct result every time — step 3 fails, `app_crashed: true`, the `.ips` is captured — ruling out a defect in `CrashView.swift`, `AppModel`'s `SHOWCASE_CRASH` wiring, `_classify_app_crash`, or the runner's handle-based tap. The concurrent `video-no-bytes` stall is this job's own evidence of the same degraded render/window-server pipeline #9 names; under it, XCUITest's synthetic tap can report `accepted` (event injection succeeded) without the app ever receiving it, which silently skips the crash instead of blocking the video or the next launch. This job's artifact gap (diagnostics never uploaded) means a future occurrence must be read from the log line above rather than `stall-NN-*`/`render-probe.txt` directly — worth fixing by pointing this job's `--runs-dir` sweep at (or additionally uploading) `runs/diagnostics/`. |
| 12 | Same `app-crash (xcuitest)` job, a different half of BE-0424's pipeline: the scenario fails *correctly* this time — step 3's `tap crash.alive` reports `app_crashed: true` in `manifest.json`, proving `_classify_app_crash` / `app_crash_signal()` (`XCUIApplication.state == notRunning`) worked — but `scripts/assert_app_crash_evidence.py` still fails, now on "no app-crash/ directory at …", because `xcuitest_environment.py`'s `.ips` sweep (`_collect_app_crash_reports`, bounded at the time to `_APP_CRASH_REPORT_TIMEOUT = 5.0`s) matched zero reports | the job's own step log, same artifact gap as #11: a `recordVideo did not report 'Recording started' … within 5.0s` line and its `stall diagnostics: capturing the state behind 'video-no-bytes'` follow-up, timestamped ~10s *before* the scenario's own step 0 even starts (compare the log's UTC timestamps against `manifest.json`'s per-step `started_at`, which needs converting off its own epoch) | Confirmed PR #2012 (run 35164359245, `run_attempt` 3, job 105041168033): the manifest proves the classification worked and the trigger tap landed, ruling out a `_step_runner.py` regression; the same video/render-pipeline stall #9 and #11 already name for this host class lands at the very top of this run, evidencing host-level contention concurrent with the scenario — a plausible reason macOS's async `ReportCrash` also missed the then-fixed 5s poll window even though the synthetic tap this time was delivered (unlike #11's silently-skipped variant). PR #2012 responded by raising `_APP_CRASH_REPORT_TIMEOUT` to `15.0`s, so **that is the bound a recurrence is measured against now**. Reconfirmed on the same PR (run 35174153612, job 105055314259) against that 15s bound: `manifest.json` again shows step 3's `app_crashed: true` with no `app-crash/` directory, and the job log again carries a concurrent `recordVideo did not report 'Recording started' … within 5.0s` / `stall diagnostics: capturing the state behind 'video-no-bytes'` pair (at 03:31:41, ~35s before the scenario's own steps run) — so the wider bound did not clear the host-contention case, only (as intended) the uncontended one. This job's artifact gap (see #11) means, again, the log's `video-no-bytes` line is the only confirmation available; the same `--runs-dir` / `runs/diagnostics/` fix noted in #11 would let a future occurrence be confirmed from `stall-NN-*` directly instead of by timestamp arithmetic. A recurrence carrying **no** concurrent stall line is not this pattern: that would point at the current 15s budget still being too tight for `ReportCrash` on any loaded runner — a budget to widen again in code, not a host flake. **Two such no-stall recurrences are now confirmed** (run 35185561508 job 105090800835, then again run 35190493777 job 105103033417, both against the 15s bound, neither carrying a `video-no-bytes`/`recordVideo` line anywhere in the full job log) — this PR fixed the artifact gap itself (the job now uses `--runs-dir runs/app-crash` and calls `collect-ios-diagnostics`, matching every sibling job) rather than guessing at a new timeout number, so a future recurrence carries real `stall-NN-*`/`render-probe.txt`/`host-telemetry.log` evidence to actually diagnose the 15s bound against, instead of the log-line-only confirmation this row and #11 were limited to. A fourth post-fix recurrence (run 35201232823, job 105231880185), ~4h after the third, carried exactly that evidence: `render-probe.txt` showed `screenshot: STILL RUNNING after 15s — killed (the render service is not answering)` taken *before* any scenario ran, and the stall capture's `probe.txt` showed a `sample:backboardd:<pid>` call itself timing out — direct confirmation of the render/IPC contention this pattern names, not a proxy log line, and persisting hours past a single-VM blip. That evidence still doesn't change what a wider budget can fix: it cannot help a host whose render service is fully wedged for the probe's own 15s watchdog. PR #2012 raised `_APP_CRASH_REPORT_TIMEOUT` again anyway, 15s → 30s, as a deliberate, evidence-informed mitigation for a host that recovers within tens of seconds rather than a claim that 30s beats a fully wedged pipeline — a human decision made after this direct evidence was in hand, not a blind guess-raise. |

### Android (`android-e2e.yml`)

| # | Symptom | Confirm with | Notes |
|---|---|---|---|
| 5 | The resident UI Automator server stops answering, and the driver degrades to the `uiautomator dump` subprocess | a `stall-NN-resident-read-*` capture | The trigger fires only when the channel is gone for the rest of the lease, not on momentary noise |
| 6 | The emulator's own renderer wedges — `screenrecord` runs but produces no bytes | a `stall-NN-screenrecord-no-growth-*` capture, plus `dumpsys SurfaceFlinger --latency` in it | This lane's known flake. Distinguishes a wedged renderer from a recording that never started |
| 7 | The emulator never comes up at all | the `reactivecircus/android-emulator-runner` step's own log | The single failure in `fault-injection (adb)`'s 73-run promotion window, and nothing the lane asserts. One confirmed variant (PR #2012, `smoke (adb)`, run 35252166515, job 105307465870): the action's own `sdkmanager --install 'system-images;android-34;google_apis;x86_64'` step logs `Warning: An error occurred while preparing SDK package Google APIs Intel x86_64 Atom System Image: Error on ZipFile unknown archive.`, then the emulator step fails outright on `error: could not connect to TCP port 5554: Connection refused` before `bajutsu`'s own scenario script ever runs — a corrupted download from Google's SDK package repository, not this repository's code; the PR's diff touches no Android/emulator config. A rerun is the correct response. |
| 8 | The job runs out its `timeout-minutes` with no scenario verdict | `host-telemetry.log`, which covers the whole job including a died emulator step | The composite action brackets the emulator step from outside, so this layer survives a job whose emulator step died |
| 10 | A UI value that should update right after a correctly-delivered tap never lands within the assertion's wait bound (`wait_until` times out with no other symptom) | the failing test's per-test `device.log`/logcat: a `TouchInteractionService.onInputEvent` `ACTION_DOWN`/`ACTION_UP` pair confirms the tap was delivered, immediately followed (within the wait window) by `BLASTSyncEngine: WM sent Transaction to organized, but never received commit callback. Application ANR likely to follow.`; `diagnostics/anr.log` pulls 0 files, ruling out an actual ANR | A WindowManager/SurfaceFlinger transaction-commit stall on the emulator's compositor — the same renderer-wedge fault class as #6, just surfaced through a UI-update timeout rather than a stalled `screenrecord` capture. No `stall-NN-*` capture exists for it (BE-0367's triggers don't instrument this path). Confirmed PR #2012 (`conformance (adb)`, `test_a_tap_lands_on_the_element_the_selector_named`): the PR's `adb_driver.py`/`android_environment.py` diff is pure addition (zero lines removed or modified) and none of the added code runs on the tap/`wait_until` path, ruling out a code regression |
| 14 | An `expect`'s own gesture-result assertion reads a stale value right after a `long_press`/`double_tap` (e.g. `expected equals='pressed' but actual='idle'`) | `WARNING bajutsu.adb.resident: read lag` lines in the job log immediately before the failing `expect`, naming a multi-second gap between the device's own gesture mark and the newest tree read | Confirmed run history rather than a stall capture (PR #1979): the identical branch's own `smoke (adb)` job passed cleanly ~34 minutes earlier with no Android-relevant code change between the two runs. Reconfirmed on a differently-shaped assertion (PR #2012, `conformance (adb)`, run 35253562344, job 105311875152, `test_one_scroll_step_travels_the_distance_it_was_asked_for`): not an `expect`, but a direct travel-distance measurement after a scroll gesture, that reads a scroll as having travelled `0.0` of a requested 1440px — the same `WARNING bajutsu.adb.resident: read lag` signature precedes it, three occurrences within the one test with a widening gap (-1777ms, then -14359ms, then -25432ms) between the device's gesture mark and the newest tree read, consistent with the resident channel degrading for the rest of the test rather than one momentary blip. The PR's `adb_driver.py` diff in this window is BE-0424's crash-signal additions (`app_crash_signal` / `_confirm_exit_info` / `reset_exit_info_poll`), pure addition and none of it touches the resident-read or scroll-actuation path, ruling out a code regression. |

## `e2e-known-flake` — GitHub Actions platform faults (any workflow)

Unlike the on-device lanes above, these are not simulator/emulator faults and carry no
`runs/diagnostics/` artifact to confirm them — the job log itself is the evidence, because the
failure traces to infrastructure GitHub Actions or a dependency registry it calls out to owns, not
to anything this repository's own steps control. Most of these fire inside GitHub's own
workflow-template evaluation before any of this repository's steps run (#13); one (#15) fires
inside a repository step whose command reaches an external package registry, which is still outside
this repository's control even though the step itself is ours.

| # | Symptom | Confirm with | Notes |
|---|---|---|---|
| 13 | A step fails with `##[error]The template is not valid. <workflow>.yml (Line: N, Col: N): hashFiles('...') couldn't finish within 120 seconds` | the job's own log — no artifact to download; the error is timestamped at an `actions/cache`-style step, before any `bajutsu`-specific script has run | GitHub Actions' own `hashFiles()` backend timing out, unrelated to any code in this repository. Confirmed PR #2012 (`codegen (xcuitest)`, run 35174153612): the failure landed on a cache-key `hashFiles(...)` step, immediately after Xcode version resolution and before the job reached any scenario code, on a job that had passed on every prior run of this same PR. A rerun is the correct response; there is nothing in the repository to fix. |
| 15 | `Analyze (java-kotlin)`'s `make -C demos/showcase/android compose-build` step fails Gradle's dependency resolution with `Could not GET '<url>'. Received status code 403 from server: Forbidden`, repeated for every single artifact the build needs (`kotlin-stdlib`, `kotlin-reflect`, `commons-codec`, `javawriter`, …) against both `repo.maven.apache.org` and `plugins.gradle.org` | the job's own log: every POM/JAR fetch in the build returns 403, not a handful — a per-artifact rejection would point at that one coordinate, but a blanket rejection across every registry the build touches points at the registry (or the runner's route to it) refusing the whole client | Confirmed PR #2012 (run 35250805835, job 105302464362): the failing build is Android/Gradle-only and this PR's diff touches no Android build config, no Gradle file, and no dependency version — CodeQL's own dependency-extraction step (`codeql database create`) invokes the ordinary project build unmodified, so a code change in this PR cannot explain every coordinate being rejected by the registry at once. A rerun is the correct response; there is nothing in the repository to fix. |

## What is deliberately not here

**Pixel visual-regression drift** (the `visual` jobs) and **element-tree golden drift** (the `golden`
jobs) are neither flakes nor regressions in the ordinary sense. A pixel baseline is host-specific and
a golden can drift with an upstream on-device dependency, which is exactly why neither job feeds a
required aggregator ([docs/ci.md](../../../../docs/ci.md), "Which E2E checks gate a merge"). A red
`visual` or `golden` is a signal to re-record or to investigate the upstream, and a human decides
which. Report it as `e2e-unclassified` and say the job is a non-gating signal, rather than
recommending a re-run that would change nothing.
