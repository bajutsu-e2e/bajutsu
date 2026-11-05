**English** · [日本語](BE-XXXX-adb-resident-read-timeout-recovery-ja.md)

# BE-XXXX — Keep the Android resident server through a read timeout

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-adb-resident-read-timeout-recovery.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2349](https://github.com/bajutsu-e2e/bajutsu/pull/2349) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

One slow screen read no longer costs an Android run its resident UI Automator server, the
instrumentation process that Bajutsu keeps on the device to read the screen and act on it. Before
this item, one read that outlasted the host's 5-second timeout switched the rest of the lease to
`uiautomator dump`. We bound every wait on the device so that a read fits inside that timeout. We
also retry and restart the server before we give the channel up, and we record any fallback in the
run's artifacts.

## Motivation

Roughly one Android run in several printed this warning partway through, on no particular scenario:

```text
resident hierarchy read failed (resident channel unreachable on port <port>: timed out); falling
back to 'uiautomator dump' for reads and coordinate injection for gestures, for the rest of this lease
```

A non-ASCII `type` step then failed, because `uiautomator dump` cannot serve that text. Running the
same scenario again on the same emulator passed.

The cause is a mismatch between the two ends of the channel. `UiDevice.waitForIdle()` waits up to
10 seconds by default, and a screen that keeps publishing accessibility events holds the wait to
its full length. One `GET /source` ran that wait 12 times:

| Where | Waits |
|---|---|
| `respondSource`, before the dump | 1 |
| between the 4 settling dumps | 3 |
| inside each dump, where `dumpWindowHierarchy` calls it twice | 8 |

We measured this on an API 34 emulator showing the Clock app's running stopwatch. One read took
120.9 seconds against the host's 5-second timeout, and the server could not start at all within
200 seconds.

Three more facts made one timeout expensive:

- **The driver read a timeout as a dead channel.** The server was alive and still working, yet the
  driver switched the rest of the lease to the fallback.
- **A naive retry could not help.** The server handles one connection at a time and finds out that
  the host gave up only when it writes the reply. A new connection therefore queues behind the
  request it replaces, for as long as that request runs.
- **The fallback is worse than slow on such a screen.** `uiautomator dump` fails there with
  `ERROR: could not get idle state`. In our run, the driver spent 66 seconds on those failures and
  returned an empty tree.

Keep-alive reuse was another suspect, and we ruled it out. `_channel` retires a connection on any
exception, and a test now pins that behavior.

After this change, a run on a never-idle screen stays on the resident channel, and each read
returns within the host's timeout. A later reader can check this on the measured screen: one read
now takes about 3.6 seconds. A run that does fall back says so in `manifest.json`.

## Detailed design

### Unit 1: the wait budget

`bajutsu/common/backend_cli/adb_resident/budgets.py` holds the numbers for both ends. The host sends
its budget with every request that waits, as `budgetMs`, and the server cannot exceed its own
default even when asked to.

| Name | Value | Meaning |
|---|---|---|
| `READ_TIMEOUT_S` | 5.0 s | the host's socket timeout on a read, unchanged |
| `WAIT_BUDGET_MS` | 3,500 ms | what one request may spend waiting on the device |
| `ACTION_TIMEOUT_S` | 10.0 s | the host's socket timeout on `/act` and `/type`, unchanged |

Passing an argument to `waitForIdle` would not have been enough, because the two waits inside
`dumpWindowHierarchy` take no argument. The server's `WaitBudget` class therefore sets
`Configurator.waitForIdleTimeout`, the one setting both kinds of wait read. The setting is
process-wide, which is safe because the server handles one request at a time.

`WaitBudget` splits the budget as follows:

- One idle wait takes at most 1 second, so the first wait cannot spend what the read-mark wait
  needs.
- A dump's two internal waits share what remains.
- `settled` stops taking more dumps once the budget is spent, and it returns the tree it has.

These bounds cut a wait short and never replace it with a sleep, so a screen that goes idle releases
the wait at once. A dump always runs at least once, and nothing can interrupt it. A real dump took
tens of milliseconds on the emulator, which leaves the 1.5 seconds of room far from tight.

`POST /act` holds the same budget before it injects. After the gesture lands, it waits at most
500 ms for the publish and 1.5 seconds for the tree it returns. A press-and-hold's own duration is
added to the host's timeout, because the server cannot answer while it holds the press.

`tests/test_adb_resident_budgets.py` keeps the two ends in step. The gate builds no Kotlin, so the
test reads the server's constants from its source and checks that each endpoint's waits fit inside
its timeout. The test also fails on a new `device.waitForIdle()` outside `WaitBudget`.

### Unit 2: the stage timings

`WaitBudget` logs where each request's time went, under the tag `BajutsuResidentServer`:

```text
source waits: idle=1074ms dump=2140ms idle=330ms dump=25ms total=3570ms budget=3500ms
```

The line goes out before the reply, so a request the host has abandoned still leaves its record. A
request that used its whole budget logs at warning level, and any other request logs at debug level.

### Unit 3: recovery from a timeout

A timeout now raises `AdbResidentTimeout`, a subclass of `AdbResidentError`. A refused or closed
connection keeps raising the parent class. `ResidentServer` then handles the two differently:

| Fault | What the lease does |
|---|---|
| Timeout | reads again on a fresh connection, then restarts the server and reads once more |
| Timeout after all of that | stops the server and falls back to `uiautomator dump` |
| Refused or closed connection, at any point | stops the server and falls back at once, as it did before this item |

A lease restarts its server at most twice in a row, and a read that answers on its first try clears
the count. Without this bound, a restarted server that answers one read and then wedges again would
cost every read a restart and never reach the fallback. When the operator has opted in to stall
diagnostics, the lease captures the device's state before each restart, because the restart destroys
the wedged server.

Repeating a read is safe because a read changes nothing on the device. The server must stop before
the fallback, because a dump taken beside a live server reads an empty tree. A restart forwards a
new port, so the channel's callables read the port per call instead of capturing it.

A restarted server seeds its read mark from the current device clock, so the first read after a
restart counts as newer than the pending gesture. That reading is sound, because the fresh session
takes its dump after the gesture.

The target's config sets the retry count and whether a restart is allowed, beside `nativeZ`:

```yaml
driver:
  residentRecovery:
    readRetries: 1   # 0 to 5; 1 by default
    restart: true    # true by default
```

### Unit 4: reviving the server for non-ASCII text

The dump path can read and tap, but it cannot enter non-ASCII text. A non-ASCII `type` step on a
lost channel therefore starts the server again first. Only a failed start produces today's error. A
driver tries this once per loss, so later steps do not pay for another failed start.

A device group's drivers share one server, so one driver can revive the server that another driver
lost. Each driver therefore checks at the start of a read whether the server is up, and it rejoins
the channel when it is. Rejoining restores what the driver knew before the loss, so an older
server's missing `/act` stays recorded as missing.

### Unit 5: the record in the run's artifacts

The driver reports through a new opt-in protocol, `ChannelHealthReporter`, and `manifest.json` moves
to `schemaVersion` 18:

| Field | Where | Meaning |
|---|---|---|
| `warnings` | each step | what the channel did during that step: a recovery, a loss, or a return |
| `channel_degraded` | each scenario | why reads were on the dump path when the scenario ended, empty when healthy |
| `channel_notes` | each scenario | what the channel did after the last step drained |

`channel_degraded` is what finds a scenario that passed on the fallback. Nothing on the verdict path
reads these fields, so a run's result does not change (prime directive 1).

### Limits

- A server that never came up at the start of a lease is recorded but not revived, because the
  driver holds no channel to revive. The bound in unit 1 makes this start failure much less likely.
- A single dump that stalls on its own can still outlast the host's timeout, because nothing can
  interrupt it. Unit 3 is the recovery for that case.
- A read on a never-idle screen takes about 3.6 seconds. Each read is slow, but it succeeds.
- A wedged server costs a read about 18 seconds, spent on two timeouts and the restart.
- We verified these paths on one API 34 emulator, and the Android end-to-end lane has not run them.

## Alternatives considered

- **Raise the host's timeout.** A timeout above the worst case would have to exceed 120 seconds, so
  a wedged server would hold a step for that long. The value would also stop being an upper bound
  the moment a library default changed.
- **Pass a timeout to `waitForIdle` alone.** This leaves the two waits inside `dumpWindowHierarchy`
  at 10 seconds each, which still overruns the host's timeout on the first dump.
- **Run the dump on a worker thread and answer `503` at the deadline.** This would bound the dump
  too, but a later request would have to wait for that worker, because a second traversal could
  collide with the first. Unit 3 already covers the rare stalled dump with far less machinery.
- **Define the budget twice and compare by hand.** Sending the budget on the request gives the host
  sole ownership of the number. The server keeps a default purely for a request without one.
- **Retry on any channel fault.** A refused connection shows that the server cannot answer, and
  retrying merely delays the fallback that every later read takes anyway.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1: bound every device-side wait, and pin the budgets against the host's timeouts
- [x] Unit 2: log each request's stage timings on the device
- [x] Unit 3: retry and restart after a timeout, configured by `driver.residentRecovery`
- [x] Unit 4: revive the server for non-ASCII text, and rejoin a server that is back up
- [x] Unit 5: record the channel's history in `manifest.json`

## References

- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md):
  the resident server and its fallback.
- [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md): the read mark and the
  `POSTDATE_BUDGET_MS` wait.
- [BE-0339](../BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation.md): `POST /act` and
  the latch this item makes recoverable.
- [BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md): the
  opt-in stall capture, whose logcat tail now carries the stage timings.
- [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md):
  the kept-alive connection.
- [BE-0476](../BE-0476-android-non-ascii-text-entry/BE-0476-android-non-ascii-text-entry.md): the
  `POST /type` route that made this fallback fail a step.
- The steps to reproduce a slow read are in
  [`BajutsuAndroidUIAutomatorServer/README.md`](../../BajutsuAndroidUIAutomatorServer/README.md#measuring-a-slow-read).
