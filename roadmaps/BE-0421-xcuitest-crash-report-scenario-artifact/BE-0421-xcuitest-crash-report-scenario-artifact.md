**English** · [日本語](BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)

# BE-0421 — Copy the iOS runner's crash report into the failed scenario's run directory

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0421](BE-0421-xcuitest-crash-report-scenario-artifact.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0421") |
| Implementing PR | [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999) (units 1-6) |
| Topic | Platform support |
| Related | [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md), [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md), [BE-0415](../BE-0415-driver-call-trace-per-scenario/BE-0415-driver-call-trace-per-scenario.md) |
<!-- /BE-METADATA -->

## Introduction

The resident XCUITest runner sometimes dies mid-scenario. The crash-recovery retries of the run
pipeline sometimes exhaust without a recovery. When that happens, `bajutsu run` builds the failure
message of the scenario from the crash error alone. The message names the fact that the runner died.
It carries no runner-log path and no log content. It does not mention the report that macOS itself
writes for a process that faults.

Two pieces of direct evidence do exist on disk at that moment:

- the captured output of the runner itself
- the crash report that macOS wrote, when the host process itself faulted

Neither reaches the run directory of the scenario (`runs/<run_id>/<sid>/`), where every other piece
of that scenario's evidence already lives.

This item copies both into that directory. A contributor debugging a red scenario then finds the
failure evidence of the runner next to the screenshots and element trees that the same run already
produced. The contributor no longer digs through warning-level logs for a path. The contributor no
longer re-runs the scenario with extra diagnostics enabled just to see what the runner process did.

## Motivation

[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) already
built a layered diagnostics collection for the iOS lanes of continuous integration (CI). Its first
layer is a result bundle per runner spawn plus a bounded stall-time probe. That layer is opt-in,
behind an environment variable that a CI workflow sets.

The other two layers are not opt-in. A composite GitHub Actions step runs on every iOS job,
unconditionally. It sweeps the crash-report and log stores of the macOS host itself
(`.github/actions/collect-ios-diagnostics/action.yml`). So CI already gathers a runner crash report
today, in the common case.

That collection still leaves two gaps, which this item closes.

1. **It is job-wide, not scenario-wide.** It tars up everything that `~/Library/Logs/DiagnosticReports`
   held for the whole job. A job that ran a dozen scenarios gives a contributor no way to tell which
   crash report belongs to which failing scenario, if more than one crashed.
2. **It is CI-only shell code with no counterpart in `bajutsu` itself.** A developer who reproduces a
   crash with a local `bajutsu run` gets nothing at all. The developer does not even get the pointer
   that the runner-log capture of BE-0319 already writes to a log file. That pointer never reaches
   the failure message or the report of the scenario. It reaches only a warning-level log line
   ([`_runner_log_hint`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)),
   which a failed run may never have surfaced.

The crash report itself exists because the host process of XCUITest
(`xcodebuild test-without-building`) is an ordinary macOS process. Sometimes it terminates on a fault
rather than an orderly exit. The operating system then writes a `.ips` crash report under
`~/Library/Logs/DiagnosticReports`. The report names the terminating signal. For system frames, it
carries a symbolicated stack trace.

A fault differs from the far more common shape that a stalled screenshot service produces, where the
process exits with a test-failure code. The `.ips` report is the one piece of direct evidence that a
genuine process fault produces. It is narrower and rarer than the ordinary "the channel stopped
answering" crash that the scope of this item otherwise covers.

This item has one observable outcome. A scenario that fails on an exhausted crash retry gains a
`crash-diagnostics/` subdirectory under its own `runs/<run_id>/<sid>/`. The subdirectory holds a
bounded tail of the captured output of the runner. Whenever macOS wrote a crash report for the
`xcodebuild` process, the subdirectory holds that `.ips` file too.

The failure string of the scenario also names that subdirectory, so a contributor who reads the
failure never has to already know it exists. Today neither the subdirectory nor that mention exists
at all.

## Detailed design

### A method every environment defines, following the shape the crash-recovery layer already uses

`RunEnvironment`
([`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py))
is a structural `Protocol`. No concrete environment subclasses it, so a method body there binds to
nobody. Every "no-op by default" method on the protocol is written out separately in each concrete
class. That includes `request_device_replacement` and `replaced_device` (BE-0354). The concrete
classes are:

- `_DeviceEnvironment` ([`ios.py`](../../bajutsu/common/platform_lifecycle/environments/ios.py)),
  which the Simulator XCUITest backend and the fake test backend share
- `WebEnvironment` ([`web.py`](../../bajutsu/common/platform_lifecycle/environments/web.py))
- `AndroidEnvironment`
  ([`android_environment.py`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py))

This item adds `take_crash_snapshot()` to the declared shape of the protocol and to all three
classes. Each class hands back a thunk that answers with nothing. That is a real, callable no-op
rather than a null, the shape that `bridge_collector` already uses on this same surface:

```python
def take_crash_snapshot(self) -> Callable[[], list[tuple[str, bytes]]]:
    """Hand the releasing lease the crash evidence this environment captured, and forget it."""
    return lambda: []
```

The method hands back a thunk rather than the bytes, because the two halves resolve at different
times. It *takes* rather than reads, because the environment is shared across scenarios while the
evidence belongs to one scenario. Both points are argued below. `XcuitestEnvironment` overrides the
method, the same way it already overrides the `request_device_replacement` of `_DeviceEnvironment`.

### Snapshotting at crash detection, not reading live state later

The crash-recovery retry loop of the pipeline is `_run_one_impl`
([`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py)). It releases the
crashed lease back to the pool with `free.put(udid)`. The release happens inside the `finally` block of
`_run_on_lease`, *before* the loop decides whether to retry.

Suppose a `crash_artifacts()` read `self._runner_log` and `self._runner_proc` lazily, only when the
loop finally gives up. It would race a concurrent worker on a multi-device run. The next lease of
that worker can reuse this exact environment instance, because `pool.py` keys its warm cache by udid.
The worker can respawn a fresh runner on it before the retry loop of this scenario reads back the old
crash's evidence. The respawn clears `self._runner_log` and `self._runner_proc` (`_discard_runner`,
`xcuitest_environment.py:1142`). For a healthy respawn, it leaves nothing crash-related behind at all.

So `XcuitestEnvironment` captures eagerly, at the last moment a crashed runner is still the runner of
this scenario. That moment is **`end_lease`**, not the discard.

- On a mid-run crash, the only action of the pipeline is `lz.release()`. The call reaches
  `pool.release()`. For a Simulator whose resident the pool keeps warm, it then reaches
  `XcuitestEnvironment.end_lease`, which terminates the app and nothing else.
- The discard that would otherwise notice the dead runner does not run until the *next* bring-up,
  inside `start()`. That is after `free.put(udid)`. It never runs at all on a run whose retries are
  already spent.
- A capture hooked at the discard would therefore be mistimed. For `crash_retries: 0`, it would be
  absent.

`_discard_runner` carries the same hook as a second site. A real-device lease, an actuator switch, a
pool shutdown, and the cleanup of a failed lease all release through `teardown` instead.

Both sites share one predicate, `_runner_crashed()`. It asks exactly the two signals on which
`_runner_alive` declares a mid-run crash:

- the `xcodebuild` leader exited
- the capture of the leader carries the marker saying that the XCTest run ended while the process
  lingers

The second signal is what an older, narrower formulation of this design missed. That formulation
gated on the `crashed` flag of `_discard_runner`. The flag is set only inside the
`exited is not None` branch. The dominant CI shape is a stalled screenshot service, which leaves
`poll()` answering `None` throughout. That formulation would have captured nothing for it.

The `_discard_runner` hook stays gated on `warn_on_crash` on top of the predicate. A cold-spawn-failure
discard is explicitly *not* a mid-run crash. It must not overwrite the evidence of a crash with its
own, mid-retry-loop. Two places perform such a discard: `_spawn_cold_with_retry`, and the
`discard=lambda: self._discard_runner(warn_on_crash=False, keep_log=True)` wired into the driver.

One crash shape stays out of reach. This design declares it rather than papering over it. A channel
can stop answering while `xcodebuild` runs on and writes no run-ended marker. That raises a
`BackendCrashError` that neither liveness signal sees. It is the wedged-Simulator degradation that
BE-0354 escalates to a replacement device. The stall probe of BE-0361 already captures it on its own
trigger. This item leaves the shape to those two items. Covering it here would pay a bounded log read
on every lease, green ones included.

At each site, before `self._runner_proc = None` clears the handle, the environment captures two
different things at two different times. The runner log and the `.ips` crash report have different
failure windows to race.

- **The captured output of the runner itself.** The environment reads it *eagerly, right there*, as
  a bounded tail from `self._runner_log`. The read is the same streaming `deque(fh, maxlen=...)` that
  `_runner_log_hint` already uses to avoid materializing the whole, high-volume capture
  (`xcuitest_environment.py:1134-1137`). The cap is larger than the twenty-line cap of that hint,
  because this artifact exists to exceed the hint. This read races `free.put(udid)`, so it cannot
  wait.
- **The match criteria for the macOS crash report of the `xcodebuild test-without-building`
  process.** The environment freezes them *eagerly, right there* too. The report itself is read only
  later.
  - `_spawn_runner` already records the process handle as `self._runner_proc` right after `Popen`
    returns (`xcuitest_environment.py:769`).
  - This item adds a spawn timestamp next to it, `self._runner_spawned_at = time.time()`, because
    `Popen` exposes no start time of its own.
  - Both values are copied into a dedicated pair, `self._last_crash_report_match`, frozen at this
    exact moment. The pair is distinct from the live `self._runner_spawned_at` and
    `self._runner_proc`. A respawn on this same environment instance would otherwise overwrite those
    two before anyone reads them back.

Both entries are cached as `self._last_crash_artifacts`. The cache mixes the already-read log tail
with the frozen match criteria. The pipeline calls `crash_artifacts()`, which reads that cache. The
runner log comes back unchanged. The `.ips` lookup happens **only then**, against
`self._last_crash_report_match`.

That deferral is deliberate. The `ReportCrash` service of macOS writes a `.ips` report
*asynchronously*, after the faulting process is already gone. Symbolication can take anywhere from
hundreds of milliseconds to several seconds. Listing `DiagnosticReports` synchronously at the capture
site would usually find nothing yet.

The call site of the pipeline runs only once the retry loop has already given up. That is seconds
later and off the respawn path, which gives `ReportCrash` the time it needs. The frozen match
criteria make the deferral safe. By the time `crash_artifacts()` runs, `self._runner_spawned_at` and
`self._runner_proc` may already belong to a different, later spawn. `self._last_crash_report_match`
still names the crashed one.

The lookup lists `~/Library/Logs/DiagnosticReports` for a `.ips` file named `xcodebuild-*`. The
modification time of the file must fall at or after the frozen spawn timestamp. The name-and-time
match keeps the sweep from picking up the report of an unrelated `xcodebuild` invocation, left on the
same host.

The header of the file is JavaScript Object Notation (JSON). When it names a `pid`, matching it
against the frozen pid narrows the choice further on a multi-worker host, where several `xcodebuild`
processes run concurrently. A report whose header cannot be parsed is still taken on the
name-and-time match alone.

The listing is capped at the first few matches, mirroring the per-capture caps of BE-0361. A
runner that keeps crash-looping cannot make the evidence write of one scenario unbounded.

Both entries stay best-effort throughout. `_result_bundle_path` and `_capture_stall` take the same
posture for their own captures. A missing log resolves to that entry being skipped, and never to a
raise. So does an unreadable `DiagnosticReports` directory, which any platform but macOS or a
sandboxed CI runner with no read permission produces. So does a report that never showed up in time.

This ordering is what a released, then re-leased, environment cannot undo. The snapshot is already
taken and cached before `free.put(udid)` runs. The state changes of a later respawn never touch it.

Caching on the environment is only half the story, though. The environment outlives the scenario,
because the pool keeps it warm per device. Evidence left there is visible to whichever scenario
leases that device next, and that scenario can destroy it. Under `workers > 1`, this cuts both ways.

- A second crash on the shared environment would overwrite the snapshot of a first scenario before
  its retry loop read the snapshot.
- A merely *healthy* intervening lease would be enough to strand the snapshot. The loop of a
  scenario can spend minutes not reading, for example when a forced-erase prep hit a `DeviceTimeout`
  (BE-0374).

So the evidence does not stay on the environment at all. **Ownership moves to the releasing lease.**
`take_crash_snapshot()` hands back a thunk and clears what it handed over. The `release()` of
`pool.py` calls it once. It calls it after the teardowns that let the environment observe a crash,
and before `free.put(udid)` offers the device to anyone else. The `Lease` then reads its own copy.

`video_start_stalled` already follows that rule, for the same reason that `pool.py` states for it: a
`workers > 1` run must never read one scenario's signal on another's retry. The thunk still defers
its `.ips` lookup. What it no longer does is read state that a later lease can change.

The capture itself happens per spawn, at most once, tracked by `_crash_snapshotted`. Two sites
observe one crash: the release, and any later discard that still finds the dead runner. A second
capture would regenerate evidence that the releasing lease had already taken. It would put that
evidence back on the environment, for the next scenario to inherit.

### Reaching the scenario's own writer

`_run_one_impl` already holds `sid`, the name of the evidence directory of the scenario. It reaches
the environment of the crashed attempt through the `Lease` that the attempt ran on. It does not use
`lz` for this, because that variable is reset to `None` at the top of every attempt. Two kinds of
loop would find `lz` empty while an *earlier* attempt's environment still holds the evidence that this
scenario failed for: a loop that ends on a lease-time crash, and a loop that ends on a forced-erase
prep that timed out (BE-0374).

The loop therefore keeps the crashed lease in a variable of its own, `crashed_lz`. The
`except BackendCrashError` handler assigns it.

`Lease` ([`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)) gains one more
field. The field has a module-level no-op default, the way its `relaunch` and `control` neighbors
already do:

```python
def _no_crash_artifacts() -> list[tuple[str, bytes]]:
    return []

# on Lease:
crash_artifacts: Callable[[], list[tuple[str, bytes]]] = _no_crash_artifacts
```

The `lease()` closure of `pool.py` holds a lease-local thunk beside the `Lease` it builds. The thunk
starts at that same no-op. `release()` reassigns it from `lease_env.take_crash_snapshot()`.

`request_device_replacement` is wired as a bound method reference, because the *device* is what it
acts on. This thunk must not stay bound to the environment, unlike that method. The environment is
shared across scenarios, and the evidence belongs to one scenario.

The pipeline asks for the evidence once. It asks where the retry loop gives up and builds the
terminal `RunResult(ok=False, ...)` of the scenario, for a backend crash it could not recover. That
is the `if device_timeout ... elif ... else:` chain that already sets `failure` for that case.

Right after that chain, the pipeline checks two conditions: `crashed_lz is not None`, and
`self._artifacts()` returns a writer. If both hold, it calls `crashed_lz.crash_artifacts()` before it
returns the `RunResult`. The call sits after the chain rather than inside its crash branches, for
two reasons.

- A loop that ended on a device-preparation timeout was still recovering from a crash, and the
  evidence of that crash explains the timeout too.
- A scenario that never crashed leaves `crashed_lz` as `None` and reaches no write at all.

For each `(name, content)` pair, the pipeline writes through
`writer.write_text(f"{sid}/crash-diagnostics/{name}", content.decode(errors="replace"))`. This is the
same per-scenario write shape that
[BE-0415](../BE-0415-driver-call-trace-per-scenario/BE-0415-driver-call-trace-per-scenario.md)
already uses for `driver_trace.json`. The pipeline uses `write_text` rather than `write_bytes`, so
the free-text scrub (BE-0331) still runs over content that this item did not author.

The pipeline also appends the path of the subdirectory to `failure` itself. The failure string that a
contributor actually reads then points at the new evidence directly, which closes the gap that
Motivation named.

A logger catches a write problem, such as a full disk or a permissions error. A write problem never
raises. A diagnostic artifact must never turn an already-decided failure into a different, unrelated
one.

### Why once, not per attempt

The stall probe of BE-0361 fires at every crash. It investigates whether the *retry itself* is worth
trying. This item fires once, and only when the retry loop has already given up.

The evidence of this item is the crash of the runner. That crash names the same fact on every
attempt of one crash-looping scenario. Copying it after each attempt would leave a recovered scenario
carrying crash evidence for a failure that its own final result no longer reports. A scenario that
recovers within its retry budget passes, and a passing scenario needs no crash report.

### Cost on every other backend

The environments of Android and the web backend return `[]` from their own `crash_artifacts()`. The
write step of the pipeline becomes a no-op list iteration, unchanged from today. The iOS backend
itself only ever constructs on macOS, so the `DiagnosticReports` sweep never runs on a Linux host at
all. Nothing in this item changes what a non-macOS run captures.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Gate the capture behind an environment variable, matching `BAJUTSU_XCUITEST_RESULT_BUNDLES` / `BAJUTSU_STALL_DIAGNOSTICS` of BE-0361 | The gap of this item is the opposite of the gap of layer 1 in BE-0361. CI already gathers a crash report unconditionally (layers 2–3). What is missing is a local-run counterpart and per-scenario attribution, and an opt-in CI variable serves neither. The capture also runs only once a scenario is already ending in failure. Its cost is one bounded directory listing and one file copy, not a standing overhead worth gating. |
| Also copy the `.xcresult` result bundle that unit 1 of BE-0361 can produce | That bundle is already the opt-in artifact of BE-0361, behind `BAJUTSU_XCUITEST_RESULT_BUNDLES`. It is not bounded the way a `.ips` report is. Making it default-on here would add an unbounded artifact for content that the `.ips` report and the runner's own log already summarize. A future item can add it if the evidence of this item proves insufficient. |
| Capture at every crash-recovery attempt, not only the one that exhausts the budget | Rejected in *Why once, not per attempt* above. It duplicates evidence for a scenario that eventually recovers. The crash-exhausted `RunResult` of the scenario is the one place this evidence explains, and it exists only once, at the end of the loop. |
| A full `xcrun simctl diagnose`, `log collect`, or `sysdiagnose` sweep on crash | Rejected for the reason that *Alternatives considered* in BE-0361 already gave and measured. `simctl diagnose` alone runs to 22–78 MB and about 15 seconds per booted device. A `.logarchive` of the whole window of a job runs to hundreds of megabytes. The `.ips` report of the process already names the terminating signal and, for system frames, a symbolicated stack trace. It costs a few kilobytes. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

Log:

- [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999) — Units 1-6. Shipped the whole item.
  Two corrections to this design landed with it, both found by the self-review pass: the capture site
  is `end_lease` plus `_discard_runner`, not the discard alone (a mid-run crash releases without one,
  and with `crash_retries: 0` no discard ever runs), and the guard is a `_runner_crashed()` predicate
  rather than the discard's own `crashed` flag, which misses the shape where `xcodebuild` lingers past
  its ended test run. Ownership of the snapshot moves to the releasing lease
  (`take_crash_snapshot`), because the environment is kept warm per device and evidence left on it is
  both readable and erasable by the next scenario to lease that device. The wedged-but-alive crash
  shape is left to BE-0354 / BE-0361 and declared as such in `docs/ci.md` rather than implied covered.

- [x] Unit 1 — `take_crash_snapshot()` on the `RunEnvironment` protocol shape and, returning a thunk
      that answers `[]`, on `_DeviceEnvironment` (ios.py), `WebEnvironment`, and `AndroidEnvironment`.
- [x] Unit 2 — `XcuitestEnvironment`'s override: a spawn timestamp (`self._runner_spawned_at`)
      recorded alongside `self._runner_proc`; a `_runner_crashed()` predicate asking the two signals
      `_runner_alive` declares a crash on (the leader exited, or it lingers past a run its capture says
      has ended); the snapshot taken on that predicate at both sites a crashed runner is let go —
      `end_lease` (the crash release path) and `_discard_runner`, additionally gated on `warn_on_crash`
      so a cold-spawn failure never overwrites a real crash — at most once per spawn
      (`_crash_snapshotted`), and handed to the releasing lease by `take_crash_snapshot()`, which clears
      what it hands over so no later scenario inherits or destroys it. The snapshot is an eager
      bounded-tail runner-log read cached as `self._last_crash_artifacts`, plus the `.ips` match
      criteria (spawn timestamp, pid) frozen into `self._last_crash_report_match` before either can
      be overwritten by a later spawn; the name-and-time `DiagnosticReports` sweep for
      `xcodebuild-*.ips` is deferred to `crash_artifacts()`'s own call so `ReportCrash` has time to
      write and symbolicate, both best-effort and bounded.
- [x] Unit 3 — `Lease.crash_artifacts`, defaulted through a module-level `_no_crash_artifacts`, backed
      by a lease-local thunk that `pool.py`'s `release()` takes off the environment once, after the
      teardowns that let it observe a crash and before `free.put(udid)`.
- [x] Unit 4 — The `pipeline.py` call site: invoked once, at the crash-exhausted `RunResult`, on the
      `crashed_lz` the retry loop remembered rather than the per-attempt `lz`, writing each artifact
      through `RunArtifactWriter.write_text` under `f"{sid}/crash-diagnostics/"` and appending that
      path to the `failure` string, with a write failure logged rather than raised.
- [x] Unit 5 — Docs: the CI diagnostics section BE-0361 added to `docs/ci.md` (and its `docs/ja/`
      mirror) gains a note on this default-on, per-scenario counterpart.
- [x] Unit 6 — Tests: `XcuitestEnvironment`'s crash snapshot against a stubbed `DiagnosticReports`
      directory (the name-and-time match, the pid refinement, the cap, the missing-log and
      missing-report cases); a test that a second crash on a shared environment overwrites the first
      scenario's cached snapshot, pinning the documented limitation; a `pipeline.py` test asserting a
      crash-exhausted scenario's run directory gains `crash-diagnostics/` and its path in `failure`;
      a recovered-scenario test asserting it does not; an Android/web/fake-backend test asserting the
      call site's no-op default is unchanged.

## References

- [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) — the
  CI-scoped diagnostics layers this item complements with a default-on, per-scenario one; also the
  source of the `simctl diagnose` and `log collect` costs *Alternatives considered* cites
- [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md) —
  the default-on runner-output capture this item copies into the scenario directory
- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md) —
  `request_device_replacement`, the existing method whose per-class no-op shape this item's
  `crash_artifacts()` follows, and whose device-replacement escalation bounds the narrow
  shared-environment limitation *Detailed design* accepts
- [BE-0415](../BE-0415-driver-call-trace-per-scenario/BE-0415-driver-call-trace-per-scenario.md) —
  the per-scenario `RunArtifactWriter` write shape this item follows
- [`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py) —
  `_runner_log`, `_runner_proc`, `_discard_runner`, `_runner_log_hint`, the seams this item reads
- [`bajutsu/common/platform_lifecycle/environments/ios.py`](../../bajutsu/common/platform_lifecycle/environments/ios.py) —
  `_DeviceEnvironment`, whose own `request_device_replacement` no-op this item's default mirrors
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py) — `_run_one_impl`,
  whose crash-exhausted `RunResult` is this item's one call site, and whose `_run_on_lease` releases
  the crashed lease before the retry loop decides whether to retry
- [`bajutsu/common/runner/pool.py`](../../bajutsu/common/runner/pool.py) — the `lease()` closure
  that wires `Lease.crash_artifacts` the same way it wires `request_device_replacement`
- [`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py) —
  the protocol `crash_artifacts()` joins, next to `request_device_replacement` and
  `replaced_device`
- [`bajutsu/common/evidence/sink.py`](../../bajutsu/common/evidence/sink.py) — `RunArtifactWriter`,
  the single write boundary (BE-0331) this item's artifacts cross
- [`.github/actions/collect-ios-diagnostics/action.yml`](../../.github/actions/collect-ios-diagnostics/action.yml) —
  BE-0361's already-unconditional `DiagnosticReports` sweep, whose job-wide scope Motivation
  contrasts with this item's per-scenario one
