**English** · [日本語](BE-0415-driver-call-trace-per-scenario-ja.md)

# BE-0415 — Trace Python↔driver calls per scenario, permanently

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0415](BE-0415-driver-call-trace-per-scenario.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0415") |
| Implementing PR | [#1966](https://github.com/bajutsu-e2e/bajutsu/pull/1966) |
| Topic | Driver & backend architecture |
| Related | [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md)
measured why a `tap` step is slow on the iOS Simulator and the Android emulator. The measurement used
[`trace_run.py`](../BE-0407-step-latency-driver-internal-tuning/misc/step-performance/trace_run.py),
a standalone wrapper script. Before starting `bajutsu run`, the script monkey-patches the driver
classes, the transport functions, and `subprocess.run`. It then writes every timed call to one JSON
file. That file covers the whole invocation.

The script answered the question of BE-0407, but it has two limits:

- It exists only as a script that someone has to remember to run in place of `bajutsu run` itself.
- Its output is not scoped to a scenario. A multi-scenario run interleaves every scenario's calls
  into one flat list.

This item makes the same kind of measurement a permanent, opt-in feature of `bajutsu run`. With
`--trace-driver`, every run writes one `driver_trace.json` per scenario. The file sits next to that
scenario's other evidence. It records every Python↔driver call with the following facts:

- The driver method invoked.
- How many host-device round trips the call took.
- On Android, which of those round trips fell back to a subprocess.
- A start time and an elapsed duration.
- The step during which the call happened.

Unlike `trace_run.py`, this feature monkey-patches nothing. It composes at dependency-injection points
that the orchestrator already has. Most of those points were built for testing (BE-0143) or for
BE-0407's own drain-tracking wrapper. The feature adds one small thread-local trace context for step
correlation.

### Not doing

- **The web backend (Playwright) is out of scope.** It is not a "simulator or emulator" in the sense
  that this item's motivation names. Network-layer communication with the browser is internal to
  Playwright's own Python binding. Bajutsu's driver code does not initiate it. The XCUITest and adb
  round trips are different: the driver code initiates them.
- **Screenshot and element-tree evidence-write timing (`FileSink.capture`) is out of scope.** Evidence
  capture makes driver calls internally (`screenshot()`, `query()`). The `driver` category already
  shows them. Evidence writing adds file I/O and secret-masking overhead on top. That overhead is not
  "operating the simulator or emulator", the motivation this item exists for.
- **Nothing here changes a scenario's pass/fail.** The trace file is diagnostic evidence alone,
  exactly like `--score` and `--zip` today. Prime directive 1 keeps every AI and every diagnostic off
  the verdict path. This item introduces neither.
- **A `transport` record's `response` field is a small structured outcome, never the full reply
  body.** A GET (a read) carries no `response` at all. A POST carries only `_Reply.status` (iOS) or
  `ActOutcome.acted` / `.published_mark` (Android). The element tree that an `/elements` or `/act`
  reply can carry stays out. That content is already the run's own evidence (`elements.json`).
  Copying it into every scenario's trace file would make the diagnostic artifact as large as the
  run it diagnoses, or larger.

## Motivation

BE-0407 already cut a meaningful share of step latency. Its own numbers are 0.3–0.6 s per iOS tap
step and 0.6–1.2 s per Android tap step. Both still fall short of the 250–500 ms target. Closing the
rest needs a larger, device-side executor change, tracked separately
([BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md)–[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md)).

Whoever picks up that work needs the same per-call breakdown that BE-0407's investigation used. So
does whoever investigates the next scenario that regresses in wall time. Today that breakdown costs
three pieces of friction:

- Finding `trace_run.py` inside a roadmap item's `misc/` directory.
- Remembering that its invocation is `bajutsu`'s own argument list with an extra wrapper in front.
- Splitting its one combined JSON file by scenario by hand.

A permanent CLI flag removes every one of them.

The cost of leaving the measurement as a one-off script is not hypothetical. BE-0407 is not the last
word on driver-internal latency, as its own Introduction says. Any future investigation starts from
zero without this item. A regression that a contributor notices in CI is one example. A new
backend's first performance pass is another.

This item builds the capability once. It is scoped to a scenario and integrated with the run
directory that a `run` already writes. The next investigation then runs
`bajutsu run --trace-driver` against the suite that actually reproduces the problem. It needs no
separate script. It needs no manual correlation of which timed call belongs to which scenario.

## Detailed design

### Categories measured

Three categories match what actually varies host-device round trips within one step:

- **`driver`**: one record per `Driver` protocol method call, such as `tap`, `query`, `screenshot`,
  or `wait_for`. It also covers two capability-protocol methods that `trace_run.py` measured:
  `drain_interruptions` and `settled_query`. A driver has these two methods only when it opts into
  the matching capability protocol (described below). The orchestrator calls them directly on a
  concrete driver.
- **`transport`**: one record per host-device round trip *inside* a single driver method call. A
  `query()` call that also fetches `/zorder` produces two `transport` records under one `driver`
  record. A `tap()` call that resolves cleanly produces one.
  - A record for a POST round trip (an actuation, not a read) additionally carries the small
    structured outcome that the device already returns.
  - On iOS, that outcome is `_Reply.status` (`bajutsu/common/drivers/xcuitest/_reply.py:22`), for
    example `"ok"` or `"stale"`.
  - On Android, it is `ActOutcome.acted` / `.published_mark`
    (`bajutsu/common/drivers/adb/act_outcome.py:25-26`).
  - The record never carries the full response body. `_Reply.raw` stays out. So does the tree that
    an `act()` reply can carry (`want_tree`). A trace stays a small, per-call log. It is
    not a second copy of the run's own evidence capture.
- **`subprocess`**: Android only. It covers two kinds of call:
  - A `uiautomator dump` fallback
    ([`bajutsu/common/drivers/adb/adb_driver.py:457`](../../bajutsu/common/drivers/adb/adb_driver.py)).
  - An `adb shell` probe (`id_u`, `getevent`, `wm size`) issued through the same seam
    (`adb_driver.py:1148,1159,1190`).

  The `simctl` and `xcodebuild` process spawns on iOS happen once at device lease. That is before any
  scenario's steps run. They carry no per-step signal and are not measured.

Every record carries `{category, step, name, started_at, elapsed_s, attempt}`:

- `step` is the index and kind of the step during which the call happened. Its form is
  `f"{i:02d}:{kind}"`, matching `trace_run.py`'s own key.
- `started_at` is a wall-clock Unix timestamp. A record can be cross-referenced against
  `device.log` or against the step's own `started_at` in `manifest.json`.
- `elapsed_s` is the call's own duration.
- `attempt` is the crash-recovery attempt (1 for the first) that made the call.

One `driver_trace.json` spans every attempt that a scenario takes. A step key alone cannot
tell a crashed attempt's records apart from the attempt that recovered. For the same reason, every
`steps` entry carries an `attempt` too.

A `transport` record for a POST round trip carries one further field, `response`. It holds the small
structured outcome described above. A GET (a read) never carries it.

### Composition over monkey-patching

`trace_run.py` has to monkey-patch, because it is an external script that cannot edit `bajutsu`'s own
source. This item can edit that source. The orchestrator already exposes the seams that a permanent
version of the same measurement needs. None of the seams is new. All were added for testability or
by BE-0407 itself:

1. **`TracingDriver`** (new) is a `base.Driver`-conforming delegating proxy that implements
   **only** `__getattr__`. It declares no protocol methods of its own.
   - Looking up an attribute checks whether the wrapped driver actually has it. The check is a plain
     `getattr`, which raises as the unwrapped driver would when the attribute is missing.
   - For a name on the traced list, the lookup returns a timed wrapper. The bare attribute is not
     returned. Everything else passes through unchanged.
   - This matters beyond `tap`, `query`, and `screenshot`. `drain_interruptions` and `settled_query`
     are not extra, driver-specific methods. They belong to two `@runtime_checkable` capability
     protocols: `base.InterruptionPolicyTarget` and `base.SettledReadProvider`
     ([`bajutsu/common/drivers/base/`](../../bajutsu/common/drivers/base/)).
   - The orchestrator probes these protocols with `isinstance`, not `hasattr`. The probes sit at
     `gestures.py:212`, `waits/_functions.py:273`, and `loop/_step_runner.py:166`.
   - A proxy that declared those two as real methods would answer `isinstance` `True` for every
     driver it wraps. That includes `FakeDriver` (no `settled_query`) and `PlaywrightDriver`
     (neither). Both would take a branch that then fails inside the proxy's own `__getattr__`.
   - `TracingDriver` declares nothing of its own. `isinstance` against any capability protocol
     reads the real capability set of the wrapped driver, unchanged.
2. **A shared trace context, not a threaded parameter.** The driver that the steps run against is
   built several calls below anywhere `--trace-driver` is visible (see unit 5).
   - One alternative is to add a parameter to `backends.make_driver`. The parameter would pass
     through `launch_driver`, `RunEnvironment`, and every concrete environment's constructor.
     That touches every backend's lifecycle class for a diagnostic feature.
   - Instead, `make_driver` and the subprocess path of `AdbDriver` (unit 4) read the same per-thread
     trace context. Unit 6 already needs that context for step correlation.
   - The context is a thread-local. `_ScenarioRunner.run_one` sets it once when `--trace-driver` is
     on.
   - Reading ambient state from a thread-local is a smaller, more contained change than adding a
     constructor parameter to every `RunEnvironment` subclass. The cost is that the seam is implicit,
     not an explicit argument. See *Alternatives considered*.
3. **`transport` on iOS.**
   [`XcuitestDriver.__init__`](../../bajutsu/common/drivers/xcuitest/xcuitest_driver.py)
   already wraps its own `self._transport` in a delegating closure, `_tracking_transport`. The
   closure folds in the drain-tracking of BE-0407 Unit 6.
   - This item adds one more optional constructor argument, for example
     `on_transport_call: Callable[[str, float, float, str | None], None] | None`. The last argument
     is `_Reply.status` on a POST and `None` on a GET.
   - The argument is applied as a second wrap in the same place.
   - The `xcuitest` branch of `backends.make_driver` passes the argument whenever unit 2's trace
     context says tracing is on.
   - No other module changes, `_raw_http_transport` included.
4. **`transport` and `subprocess` on Android, both through the same seam.** The `adb` branch of
   [`backends.make_driver`](../../bajutsu/common/backends.py) already receives `fetch_hierarchy`,
   `fetch_clock`, and `act` as plain callables from its caller. It forwards them to `AdbDriver(...)`
   unchanged.
   - When tracing is on, the branch wraps each of the three (`transport`).
   - The wrap of `act` also reads the `ActOutcome` that `act` returns into the record
     (`.acted`, `.published_mark`). It is the one Android round trip that is itself a POST.
   - The same branch does the same for `subprocess`. `AdbDriver.__init__` already takes
     `run: RunFn = adb.real_run` ([`adb_driver.py:125`](../../bajutsu/common/drivers/adb/adb_driver.py)).
     Every adb subprocess call it issues goes through `self._run`: the `uiautomator dump` fallback
     (`:457`), the `id_u`, `getevent`, and `wm size` probes (`:1148,1159,1190`), and
     `adb.Env(self.serial, run=self._run)` (`:1400,1453`).
   - Under the same condition, `make_driver` passes a timed wrap of `adb.real_run` as `run=`. Neither
     category needs a monkey-patch of `subprocess` in any module.
   - One call is outside this seam: `AdbDriver._run_text` (`:1381`, the `typeText` path). It stays
     off `self._run`, so a typed secret never reaches the adb process's argv.
   - Its own comment already says it is "routed through a class-level attribute so tests can patch
     it". The module substitutes that one attribute the same way, under the same condition.
5. **The `driver`-category wrap, at the seam that actually builds the scenario's driver.** The
   scenario's driver does **not** come from the injectable `make_driver` argument of `device_pool`
   (`Callable[..., base.Driver] = _make_driver`).
   - That parameter is read once, at [`pool.py:387`](../../bajutsu/common/runner/pool.py).
     It serves the read-only network-evidence provider (BE-0020). A run falls back to that provider
     when its own actuator does not observe network via the driver. The parameter never builds the
     driver that a scenario's steps run against.
   - The step driver comes from `launch_driver(...)`
     ([`pool.py:408`](../../bajutsu/common/runner/pool.py)), which calls `RunEnvironment.start(...)`.
   - Each environment then calls `backends.make_driver` itself. The calls sit in
     [`xcuitest_environment.py:772`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)
     and [`android_environment.py:166`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py).
   - The `lease()` closure of `device_pool` wraps the value that `launch_driver(...)` returns in a
     `TracingDriver`, right there, before storing it on `Lease.driver`. The teardown of `lease_env`
     later receives the same object.
   - The teardown call (`lease_env.end_lease` / `teardown`) could have forced a choice between two
     options. One is to wrap only the value handed to `run_scenario()`. The other is to confirm that
     the transparency of `TracingDriver` covers teardown too. It needs neither.
   - Every environment's `teardown` takes `driver` for interface shape alone and never reads it.
     Each is marked `# noqa: ARG002  # Environment shape`
     ([`environments/ios.py:88`](../../bajutsu/common/platform_lifecycle/environments/ios.py),
     [`environments/android/android_environment.py:363`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)).
     Teardown reads only environment-owned state (`self._resident`, `self._serial`, the runner
     process). `Lease.driver` can be wrapped once, uniformly, with no split between a raw
     value for teardown and a wrapped one for the step loop.
6. **Scenario and step boundaries** are two explicit calls, not monkey-patches, since this item edits
   both files directly:
   - `_ScenarioRunner.run_one` ([`pipeline.py`](../../bajutsu/common/runner/pipeline.py)) opens the
     thread-local trace context that unit 2 introduced. The context is keyed by the scenario's `sid`.
     It opens only when `--trace-driver` is on. An off run never sets it.
   - The checks in `make_driver` and in the `run=` of `AdbDriver` (unit 2) then read "not tracing" at
     zero extra cost.
   - In its `finally`, `run_one` flushes the accumulated records to `f"{sid}/driver_trace.json"`.
     The write goes through `self._artifacts()`
     ([`RunArtifactWriter.write_json`](../../bajutsu/common/evidence/sink.py)).
   - That call is the run's single write boundary (BE-0331), so secret-scrubbing runs over the
     file for free, even though no record carries scenario-authored text today.
   - `_StepRunner._run_one` pushes and pops the current step's key onto the same thread-local
     context. The file is
     [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py).
   - A thread-local is enough because `_ScenarioRunner.run_one` runs entirely on one
     `ThreadPoolExecutor` worker thread per scenario (`run_all` in `pipeline.py`). A thread is never
     mid-scenario for two scenarios at once. "The active trace context on this thread" therefore
     always means "this scenario, right now". It is also the thread on which `launch_driver` and
     `make_driver` run for that scenario's lease, so the read in unit 2 during driver construction
     sees the right scenario's context.
7. **CLI flag.** `bajutsu run --trace-driver` (`bajutsu/run/cli.py`) is threaded through `_RunPlan` to
   `_ScenarioRunner`. The `_ScenarioRunner` field gates the `open` and `flush` calls of unit 6. The
   flag follows the same "diagnostic-only, off by default" pattern as `--score` and `--zip`.

### Output shape

The trace is written to `runs/<run_id>/<sid>/driver_trace.json`:

```json
{
  "scenario": "<scenario name>",
  "steps": [{"step": "00:tap", "wall_s": 1.02, "attempt": 1}],
  "records": [
    {"category": "driver", "step": "00:tap", "name": "tap", "started_at": 1234567890.1, "elapsed_s": 0.69, "attempt": 1},
    {"category": "transport", "step": "00:tap", "name": "POST /tap", "started_at": 1234567890.1, "elapsed_s": 0.62, "response": {"status": "ok"}, "attempt": 1}
  ]
}
```

A scenario run without `--trace-driver` writes no file. The flag's off path costs nothing beyond the
flag check itself.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Promote `trace_run.py`'s own monkey-patching into a permanent module inside `bajutsu` | It reaches into private module internals (`_raw_http_transport`, `adb_resident.ResidentServer.start`). Those internals are free to change shape, since nothing outside `trace_run.py` depends on them today. Making them a permanent contract trades a script's disposable fragility for a maintained one. |
| Add timing code directly inside every `XcuitestDriver` / `AdbDriver` method body | It touches roughly twenty methods across two files that already carry real actuation logic, for a diagnostic feature. The diff is much larger and riskier than composing at the one seam (`make_driver`) through which both backends already construct. |
| Thread an explicit `trace` parameter through `backends.make_driver`, `launch_driver`, `RunEnvironment`, and every concrete environment's constructor, instead of a thread-local | This is the more conventional dependency-injection shape. An earlier draft of this item assumed that the `make_driver` argument of `device_pool` already gave it this shape (it does not; see unit 5). Wiring it explicitly touches the public constructor of every `RunEnvironment` subclass (`XcuitestEnvironment`, `AndroidEnvironment`, `WebEnvironment`, `FakeEnvironment`) for a diagnostic feature. The thread-local of unit 2 is a smaller, more contained change. The cost is that the seam is implicit, not an explicit argument. |
| Measure evidence-write timing (`FileSink.capture`) as a fourth category, matching `trace_run.py` | The driver calls that evidence capture makes (`screenshot`, `query`) are already visible under `driver`. The remainder is file I/O and redaction overhead, which is not "operating the simulator or emulator" and lies outside this item's motivation. A future item can take it up if evidence-write latency itself is ever the suspect. |
| Cover the web (Playwright) backend too, for architectural symmetry | This item's motivation is specifically simulator and emulator operation latency. Playwright's browser communication is internal to its own Python binding, not a round trip that bajutsu's driver code initiates. The `Driver` protocol boundary that a `TracingDriver` wraps would still work for `PlaywrightDriver` if a future item wants it. |
| Enable by an environment variable (`BAJUTSU_DRIVER_TRACE=1`), matching `BAJUTSU_LOG_LEVEL` / `BAJUTSU_STALL_DIAGNOSTICS` | We considered it for consistency with those two existing opt-in diagnostics. We chose a CLI flag instead. The choice is then visible in a run's own command line and in CI configuration, not in an environment that an operator has to know to check. |
| Record a `transport` POST record's full reply body, not just the small structured outcome | It would answer more questions from one trace file, for example exactly which fold `/tap` folded in. We rejected it. The body that an `/elements` or `/act` reply can carry is already captured as the run's own evidence (`elements.json`). Duplicating it into `driver_trace.json` risks a diagnostic artifact larger than the run it diagnoses. The motivation of this item (round-trip counts and durations) does not need that content. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `TracingDriver`, a `base.Driver`-conforming delegating proxy. It installs every
      member of every capability protocol the wrapped driver actually satisfies as a real instance
      attribute (derived via `typing.get_protocol_members`), keeping `__getattr__` only as a
      fallback: an `__getattr__`-only proxy — this item's original premise — can never pass a
      *positive* `isinstance` check, because a `@runtime_checkable` protocol resolves through
      `inspect.getattr_static`, which never calls `__getattr__`. `isinstance` against a capability
      protocol (`InterruptionPolicyTarget`, `SettledReadProvider`, …) still reads the wrapped
      driver's real capability set, in both directions.
- [x] Unit 2 — The shared trace context `XcuitestDriver`'s and `AdbDriver`'s own wraps consult, set
      by unit 6's `_ScenarioRunner.run_one` hook. A `contextvars.ContextVar` rather than a
      `threading.local`, matching this repo's existing ambient state (`analytics/ledger`,
      `serve/oplog`) and staying correct under `ThreadPoolExecutor` worker reuse.
- [x] Unit 3 — `transport`-category timing on iOS, inside `XcuitestDriver.__init__` itself (checked
      once, at construction, against whether a trace is already open): wraps `self._transport` a
      second time alongside the existing `_tracking_transport` wrap, recording `_Reply.status` for
      a POST.
- [x] Unit 4 — `transport`- and `subprocess`-category timing on Android, inside `AdbDriver.__init__`
      itself (same construction-time check as unit 3): wraps `fetch_hierarchy` / `fetch_clock` /
      `act` (`transport`, the `act` wrap also recording `ActOutcome.acted` / `.published_mark`) and
      the `run` callable (`subprocess`); `_run_text` is a plain `@staticmethod`, so the same wrap
      cannot reach it — it reads the ambient trace context directly instead. No monkey-patch of
      `subprocess` in any module.
- [x] Unit 5 — The `driver`-category wrap: `_ScenarioRunner._run_one_impl` mutates the already-
      returned `Lease.driver` in place with a `TracingDriver`, right after each lease call succeeds
      — not inside `device_pool`'s `lease()` closure, whose warm-driver cache stores the raw driver
      separately and would double-wrap it on reuse.
- [x] Unit 6 — Scenario and step boundary hooks in `_ScenarioRunner.run_one` and
      `_StepRunner._run_one`: open/flush the trace context (unit 2) and `driver_trace.json` write
      through `RunArtifactWriter.write_json`, in a `finally` so a scenario that raises still leaves
      its partial trace on disk; an `except OSError` around that write logs a warning instead of
      raising, so a write that raises can't end the run either — diagnostic only, like `--zip` /
      `--evidence-store`. `TraceContext` also carries an `attempt` number, stamped once per
      crash-recovery retry, so a recovered scenario's trace does not merge
      the crashed attempt's records onto the same step keys as the one that passed.
- [x] Unit 7 — The `bajutsu run --trace-driver` CLI flag, threaded through `_RunPlan` to
      `_ScenarioRunner`.
- [x] Unit 8 — Tests: a `TracingDriver` unit test against `FakeDriver`, including that wrapping
      preserves a capability-protocol negative (`isinstance(traced, base.SettledReadProvider)`
      stays False); an `XcuitestDriver` built with a stub `transport`, asserting a `transport`
      record per round trip; an `AdbDriver` built with a fake `run`, asserting a `subprocess`
      record per adb invocation; a `--trace-driver` run against the `fake` backend asserting the
      written file's shape; a no-flag run asserting no `driver_trace.json` is written. The `fake`
      backend alone would leave units 3–4 (the iOS and Android wraps) with no fast-suite coverage,
      since it produces no `transport` or `subprocess` records.
- [x] Unit 9 — Documentation: the CLI reference for `bajutsu run`'s flags, in both languages, gains
      `--trace-driver`.

Log:

- [#1966](https://github.com/bajutsu-e2e/bajutsu/pull/1966) — All 9 units, completing the item.
  `TracingDriver` (unit 1) installs every member of every capability protocol the wrapped driver
  actually satisfies as a real instance attribute, derived via `typing.get_protocol_members`, rather
  than the `__getattr__`-only proxy this item's own text describes — `isinstance()` against a
  `@runtime_checkable` protocol resolves via `inspect.getattr_static`, which never calls
  `__getattr__`, so that design could never pass a positive `isinstance` check for any protocol. The
  shared trace context (unit 2) is a `contextvars.ContextVar`, not `threading.local`, matching this
  repo's existing ambient-state convention. The `driver`-category wrap (unit 5) happens in
  `pipeline.py`'s `_run_one_impl`, mutating the returned `Lease.driver`, rather than inside
  `pool.py`'s `device_pool().lease()` closure — that closure's warm-driver cache stores the raw
  driver separately, so wrapping there would leak a wrapped driver into it and double-wrap on reuse.
  Units 3–4's transport/subprocess timing lives inside `XcuitestDriver`/`AdbDriver`'s own
  constructors rather than `backends.make_driver`'s branches.

## References

- [BE-0407 — Cut step latency by deduplicating evidence reads and tuning driver internals (iOS and Android)](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md)
- [`trace_run.py`](../BE-0407-step-latency-driver-internal-tuning/misc/step-performance/trace_run.py) — the investigation script this item makes a permanent, per-scenario feature
