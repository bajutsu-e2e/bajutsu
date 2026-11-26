**English** · [日本語](BE-XXXX-ios-ci-simulator-service-reduction-ja.md)

# BE-XXXX — Disable unneeded Simulator services on the iOS E2E lanes

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ios-ci-simulator-service-reduction.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | CI / build infrastructure |
| Related | [BE-0494](../BE-0494-simslim-ci-simulator-slimming/BE-0494-simslim-ci-simulator-slimming.md), [BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md) |
<!-- /BE-METADATA -->

## Introduction

The iOS end-to-end (E2E) jobs run on hosted macOS runners. A runner spends most of a job at a load
average in the hundreds. This item turns off Simulator services that the tests do not need. In a
first experiment, the median one-minute load fell from about 200 to about 20 in two lanes. We adopt
the change on the `run`, `actuation`, and `golden` jobs. Pass criteria fixed in advance decide it.

## Motivation

A hosted runner has a three-core virtual Central Processing Unit (CPU) and 7 GB of memory. Simulator daemons and the logging system compete for it all job long. So does the
push daemon. We looked at one job that did not pass. Its
[run](https://github.com/bajutsu-e2e/bajutsu/actions/runs/45312745997) holds the record.
Over a 339-second window,
`diagnosticd` and `apsd` took about 31％ of CPU time. The `diagnosticd` share counts both copies,
the Simulator one and the runner one. Python, `xcodebuild`, and the app took near one percent each.

We compared a stock Simulator with one that had 173 `launchd` services turned off. Both arms
rebooted once before the tests. Each lane ran three times per arm ([run](https://github.com/bajutsu-e2e/bajutsu/actions/runs/45757812584) has details). All 18
jobs passed. The table gives medians, as stock to reduced.

| Metric | `run` | `actuation` | `golden` |
|---|---|---|---|
| One-minute load | 198 to 22 | 199 to 17 | 215 to 66 |
| Job seconds | 1686 to 911 | 2472 to 1137 | 805 to 481 |

A second experiment added a boot after the downloads ([run](https://github.com/bajutsu-e2e/bajutsu/actions/runs/45791991346) has details). The reduced
Simulator kept its load median at 25 or less in all six jobs. The load median of the other two arms ranged
from 102 to 254 across those jobs. A later boot shortened the setup steps. The test steps did not improve consistently. The table
gives median test-step seconds.

| Lane | Normal order | Boot after downloads | Reduced services |
|---|---|---|---|
| `run` | 1627 | 858 | 316 |
| `actuation` | 1851 | 2054 | 695 |

Reduced jobs showed none of three stall signals:

- a recording start that was not confirmed
- a retry on the runner channel
- a Simulator reboot after the test runner did not start

Other jobs showed these signals in both experiments. The numbers carry three limits:

- Each cell holds three runs.
- The `actuation` ranges overlap.
- The sample cannot show a change in how often scenarios do not pass.

[BE-0494](../BE-0494-simslim-ci-simulator-slimming/BE-0494-simslim-ci-simulator-slimming.md)
proposed a similar reduction. Its experiment showed no speedup, and the item ended as Rejected.
Two of its three stock `run` passes took 404 and 426 seconds. The third took 2,423 seconds. The
first two are much faster than ordinary CI, and we do not know why. Boot order leaves part of the
gap unexplained. Variance between runners may explain it. BE-0494 asked a new proposal to address
three points:

| Request | Answer |
|---|---|
| Target stability | The pass criteria count stall signals, not speed. |
| Explain free memory | Not explained. See the paragraph below. |
| Use a larger sample with a fixed threshold | Unit 4 sets both. |

BE-0494 read free memory once, after the app install. Its slimmed runs read less free memory than
its stock runs. Our figure is the lowest reading over a whole job. It rose from 25–72％ to 52–83％.
The two readings are not comparable, so the earlier result stays unexplained.

We will judge success by one observable difference. Reduced jobs should report fewer
unconfirmed recording starts than unmodified jobs.

## Detailed design

This item changes CI configuration alone. The product code and the `run` verdict stay as they are.

### 1. Choose the service list

The experiment named 173 labels. That count is a ceiling, not a count of stopped services. The
committed list keeps a label when we can write a reason for it, grouped by category. These services
stay on:

- SpringBoard and `backboardd`
- notification delivery
- privacy permissions, managed by Transparency Consent Control (TCC)
- the pasteboard and location services

We measure the trimmed list again before unit 4.

### 2. Apply the change

The first choice writes the entries before the first boot. The job then still boots once. The boot
also keeps overlapping the downloads
([BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md)). `launchctl`
cannot run against a shut-down device. This route writes the undocumented launchd store of the
Simulator. BE-0494 counted that private interface as a cost. It has not had a trial yet.

The fallback runs `launchctl disable` through `simctl spawn` after boot, then reboots. It adds no
private interface. In the experiments that reboot took 73 to 253 seconds.

### 3. Wire the jobs

Turn the change on for the `run`, `actuation`, and `golden` jobs. Leave it off elsewhere. An offline
write belongs in the `boot-simulator` action. The toolchain setup action passes the input to it.
That action returns before the boot ends. The fallback runs as its own step, after each job
waits for `bootstatus`. The path that reuses a booted device states whether it applies the
change. Each job logs two numbers at the start and the end:

- the count of services turned off
- the count of Simulator processes

### 4. Decide adoption

Run the `run` and `actuation` jobs ten times with the change and ten times without it. Alternate
them in the same hours. Use fresh runs and leave out the pilot runs above. With the fallback route,
the jobs without the change reboot the same way. Each job logs which route it used. `golden` is too
short to measure load and stays out of this step.

Fix these criteria before the first run:

| Criterion | Pass condition |
|---|---|
| Unconfirmed recording start | Fewer jobs with the change, by Fisher's exact test |
| Runner-channel retries | Total with the change is not above the total without it |
| Recovery reboots | Total with the change is not above the total without it |
| Scenario results | No scenario traces back to a service that we turned off |

A job counts for the first criterion when it logs `recordVideo did not report 'Recording started'`.
The test covers both jobs together, one-sided, at 0.05. To trace a scenario that does not pass, run
it again with the suspect service left on. A pass then counts against the change.

Each job records its runner image and setup-step times. The measurement costs about 20 runner hours.

### 5. Extend the scope

Add `golden`, `conformance`, `fault-injection`, and `network` checks one at a time. A device erase
or a replacement device should drop the entries. We have verified neither case. The scenarios of the
three first jobs do not erase. A retry after a runner problem does erase the device, and a recovery
step can replace it. Count both events in the unit 4 logs. A frequent loss calls for a separate
item. That item would design how to apply the list again from configuration.

## Alternatives considered

- **A third-party slimming tool**, as in BE-0494. The fallback route needs `simctl` and `launchctl`
  alone. The offline route shares the private-interface cost of such a tool.
- **A different boot order.** It helps the setup steps, not the load during tests.
- **A larger runner.** It adds capacity at a higher cost. Nobody has measured it.
- **The stock Simulator with existing recovery.** The load stays in the hundreds. Each recovery
  reboot also repeats the boot-time work.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Choose the service list
- [ ] Apply the change before the first boot, or after it with a reboot
- [ ] Wire the `run`, `actuation`, and `golden` jobs
- [ ] Measure and decide adoption
- [ ] Extend to other lanes and decide on applying again after an erase

## References

- [First experiment](https://github.com/bajutsu-e2e/bajutsu/actions/runs/45757812584): stock against reduced Simulator
- [Second experiment](https://github.com/bajutsu-e2e/bajutsu/actions/runs/45791991346): boot order against reduced Simulator
- [BE-0494](../BE-0494-simslim-ci-simulator-slimming/BE-0494-simslim-ci-simulator-slimming.md):
  the earlier proposal and its numbers
- [BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md): why the boot
  overlaps the build
