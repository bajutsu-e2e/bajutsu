**English** · [日本語](BE-XXXX-repl-attach-running-app-ja.md)

# BE-XXXX — Attach the REPL to an already-running Simulator app

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-repl-attach-running-app.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

`bajutsu repl --target <name> --attach` connects the manual shell to an app that is already running
on a booted iOS Simulator, and leaves that app exactly as it is. The command neither erases the
device, installs a build, nor relaunches the app. The operator reads the screen they were already
looking at with `tree` and acts on it with `tap <id>`.

The XCUITest runner is the part that has to change. Today the runner calls `app.launch()` when it
starts, so every start replaces a running app with a fresh process. This item adds an attach mode
to the runner that builds the `XCUIApplication` for the running process and skips the launch. The
Python side gains a `--attach` flag, a probe that tells whether the app is running, and a launch
path that skips every device-wide step. Only the iOS Simulator (`xcuitest`) backend is in scope.

## Motivation

[BE-0423](../../roadmaps/BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate.md)
gives an operator a shell to read the element tree and tap an id without writing a scenario. The
shell starts by launching the app, and that launch is the problem. With `--udid booted`, the
default `erase` still shuts the Simulator down, erases it, boots it, installs the build, and
launches the app. Passing `--no-erase` skips the wipe, yet the runner still calls `app.launch()`
([`RunnerUITest.swift:214`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift)), which replaces
the running process with a new one.

That replacement discards what the operator came to inspect. A developer who reaches a screen by
hand, or who runs the app from Xcode with the debugger attached, loses the screen state, the
logged-in session, and the debugger connection the moment the shell starts. Diagnosing a selector
that fails on a deep screen then means navigating back to that screen after every connection. The
cost repeats on each reconnect, and it falls on the situation the shell exists for: the screen is
hard to reach, and a typed id does not resolve.

Once this ships, an operator who has an app open on a booted Simulator runs
`bajutsu repl --target <name> --attach`, and the first `tree` prints the screen that was already in
front. The observable difference is that the app's process id on the Simulator is the same before
and after the connection, and the debugger attached from Xcode stays attached. A reader can check
that against the shipped command with `xcrun simctl spawn <udid> launchctl list`.

## Detailed design

### CLI surface

`repl` gains one flag, `--attach`. The default stays as BE-0423 shipped it, so a command line
without the flag behaves exactly as before. The flag combines with `--target` and `--udid`, and
`--udid` keeps its `booted` default. `--target` is still required, because the bundle id and the id
namespaces come from the target's config, not from the screen.

| Combination | Result |
|---|---|
| `--attach` with the `xcuitest` backend on a local Simulator | The attach path below |
| `--attach` with `playwright`, `adb`, the `--udid https://…` live route, or a target with `xcuitest.deviceType: device` | Exit 2 with `repl: --attach is only supported on the local iOS Simulator (xcuitest)` |
| `--attach --erase` | Exit 2 with a message that the two flags contradict each other |
| `--attach` without `--erase` | `erase` is forced off, unlike the local default of on |

The check runs in `bajutsu/repl/cli.py` right after the actuator is selected and before
`resolve_device`. The existing live-route `--erase` check sits after `resolve_device`, so placing
the new check beside it would let an unsupported `adb` invocation query adb first and exit with an
adb error. The check reads only the raw `--udid` value (whether it is a URL) and the target config
(`deviceType`), so every unsupported combination exits 2 before any device is touched.

### Python side: probe, then attach or launch

`XcuitestEnvironment` (`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`)
receives an attach start path. It runs after the device is resolved and before any spawn, in this
order:

1. Confirm the device is booted. When none is, the command exits 2 with `repl: no booted
   Simulator; boot one first`. A named udid that is shut down exits 2 with that udid and the same
   hint. Attach never boots a device, because booting is a device-wide step.
2. Probe whether the target's bundle id has a running process. A new
   `simctl.Env.is_app_running(bundle_id)` reads `xcrun simctl spawn <udid> launchctl list` and
   looks for the `UIKitApplication:<bundle id>` entry. The command goes through the same injectable
   `RunFn` as the other `simctl` calls, so tests supply the output.
3. If the app is running, spawn the runner with `BAJUTSU_ATTACH=1` and skip
   `_prepare_simulator` entirely: no erase, boot, locale pin, install, permission grant, or
   deeplink.
4. If the app is not running, print `<bundle id> was not running; launching it` and spawn the
   runner without the variable. The runner launches the app as it does today. The launch still
   skips erase, install, and every other device-wide step. Before that spawn, a new
   `simctl.Env.app_container_exists(bundle_id)` check fails with a `DeviceError` naming the bundle
   id when the app is not installed, and the command exits 2. The runner's own `app.launch()` does
   not fail cleanly on an uninstalled bundle id, so the check cannot be left to it.

The existing `simctl.Env.is_installed` is not reused: it also returns `False` on a
`DeviceTimeout`, which would report a wedged Simulator as "not installed". `app_container_exists`
returns `False` only when the app container is absent (`get_app_container` ends in a
`CalledProcessError`) and propagates `DeviceTimeout` and every other device error unchanged.

Both paths spawn the runner with `attempts=1` and no recovery (`_no_recovery`). `_no_recovery`
stops only the recovery callback; `_spawn_cold_with_retry` still defaults to two attempts, so
`attempts=1` is needed as well. The cold-spawn retry discards each failed attempt with
`_discard_runner`, which terminates the app under test, and the recovery ladder reboots the device
and re-runs the prep. Either would destroy the app the operator asked to keep. A failed attach
spawn therefore fails once, loudly, with the runner's captured log tail, and never terminates the
target app. To that end, the discard of a failed attempt uses a new `_discard_runner` keyword,
`keep_app`, that skips `_terminate_app_under_test`.

Launch env and arguments from the target's config are forwarded on the fallback path only. An
attached app already started with whatever env it has, and the shell cannot change that after the
fact.

Readiness is skipped on the attach path as well. `readyWhen` names a launch screen, and the app is
wherever the operator left it, so waiting for that screen would time out. But `launch_driver`
(`bajutsu/common/runner/launch.py`) always calls `await_ready(..., ready_sel=eff.ready_when)`
right after `env.start`. So `launch_driver` gains a keyword argument, `skip_readiness: bool =
False`, and only the attach path of `repl` passes `True`. Every other caller keeps the default and
its behavior. With `skip_readiness` set, `launch_driver` returns a readiness outcome that records
the wait was skipped.

### Runner side: attach mode

`RunnerUITest.testServeUntilTornDown` (`BajutsuKit/Runner/Sources/RunnerUITest.swift`) reads a new
`RunnerServer.forwardedAttach` flag, the Swift counterpart of `BAJUTSU_ATTACH`
(`BajutsuKit/Sources/BajutsuRunner/RunnerServer.swift`). When the flag is set, the runner builds
`XCUIApplication(bundleIdentifier:)` for the forwarded bundle id, skips `app.launch()` and the
launch watchdog, and starts the server over the running process. The launch environment and
arguments are not applied, because nothing is launched.

A spike has to confirm one assumption before the rest is built: an `XCUIApplication` that was
never launched by the runner can still read the element tree and tap an element of a process that
was already running. If the spike shows the process must be activated first, the attach mode calls
`app.activate()` instead of `launch()`. Activation brings the app to the front without replacing
the process, so the observable outcome above still holds, though a backgrounded app would surface.

The runner still has to be installed and started on the booted device. `xcodebuild
test-without-building` does both without shutting the device down or touching other apps, so the
operator's running app is unaffected by the runner's own start.

### Exit and warm reuse

Leaving the shell already keeps the app alive: `_close_owned_session` in `bajutsu/repl/cli.py`
deliberately skips the local `xcuitest` teardown, because that teardown terminates the target app.

The current exit path does not discard the runner, though. `_spawn_runner` starts `xcodebuild` in
its own session (`start_new_session`), so leaving the shell alone leaves the runner behind, still
holding the device's automation session. An attach session therefore gets a runner-only exit:
a new `XcuitestEnvironment.release_runner()` calls `_discard_runner(keep_app=True)`, which stops
the `xcodebuild` process group and the runner app and never calls `_terminate_app_under_test`.
`_close_owned_session` calls `release_runner()` for an attach session only; every other path is
unchanged.

An attach start does not enter the warm-reuse path (`_resume_warm`), which terminates and
relaunches the app. The attach path returns a fresh driver and records that the lease did not own
the app, so no path other than the `keep_app=True` discards above can terminate it.

### Out of scope

- Real devices (`xcuitest.deviceType: device`) and the `--udid https://…` live route. A real
  device is already attach-shaped, and the live route drives a WebDriver session instead of the
  resident runner.
- `adb` and `playwright`. Neither relaunches an app as a side effect of starting its driver in the
  way the XCUITest runner does, so the problem this item solves does not exist there.
- Detecting the frontmost app without `--target`. The target config carries the id namespaces and
  `readyWhen`, and `tree` output is only useful against them.
- Attaching `run`, `record`, or `crawl`. Their determinism contract is a clean, known start, which
  attach deliberately gives up.

### Verification

The fast suite covers the Python side with fakes: flag and `deviceType` validation before
`resolve_device`, the booted check, the `skip_readiness` branch, the runner-only exit, the probe
parser against recorded `launchctl list` output, the attach-versus-fallback decision, the
not-installed check, and that neither path calls erase, install, terminate, or the recovery ladder,
including after a failed spawn. The Swift runner change compiles only in the `xcodebuild`
end-to-end job, so that job gains a case that starts an app, attaches, reads a tree, and compares
the process id before and after. A manual check runs on a dedicated Simulator, never on one
already in use for other work.

## Alternatives considered

| Option | Why it was not adopted |
|---|---|
| Skip only `erase` (a `--no-erase` default) | The runner still calls `app.launch()`, so the app restarts and the screen state is lost. It leaves the motivating problem unsolved. |
| Start the runner on a harmless seed app and retarget it with `/app/target` | The existing route needs no Swift change, but launching the seed app takes the foreground. The screen the operator was looking at changes at the moment of connection, which defeats the purpose. |
| Read the tree through `simctl` without the XCUITest runner | The shell would stop using the backend-agnostic `Driver`, so its `tap` would differ from `run`'s. Checking that a selector resolves is the shell's value, and this option gives it up. |
| Detect the frontmost app and attach without `--target` | The config supplies id namespaces and `readyWhen`, and other commands assume a target. It could be added later on top of this flag. |
| Fail when the app is not running | It is the stricter choice, since it never launches anything the operator did not ask for. The fallback was preferred because the shell stays useful when the app was closed, and the launch is announced and erases nothing. |
| Make `--udid booted` imply attach | It changes the meaning of BE-0423's default and breaks a shipped behavior. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Spike: confirm an unlaunched `XCUIApplication` reads and taps a running app (or that
  `activate()` is needed), on a dedicated Simulator.
- [ ] Runner attach mode (`BAJUTSU_ATTACH`, `RunnerServer.forwardedAttach`, the skipped launch).
- [ ] `simctl.Env.is_app_running` and `simctl.Env.app_container_exists` (a strict probe that
  propagates `DeviceTimeout`), with their tests.
- [ ] `XcuitestEnvironment` attach start path: booted check, probe, installed check, attach or
  fallback launch, a spawn with `attempts=1` and no recovery, and `_discard_runner(keep_app=True)`.
- [ ] `launch_driver`'s `skip_readiness`, and the runner-only exit through
  `XcuitestEnvironment.release_runner()`.
- [ ] `--attach` flag, its combination checks before `resolve_device` (real devices included), and
  the fallback notice in `bajutsu repl`.
- [ ] End-to-end case comparing the process id before and after attach.
- [ ] `docs/cli.md` and `docs/ja/cli.md` flag reference, plus the `repl` description in
  `docs/architecture.md`.

## References

- [BE-0423](../../roadmaps/BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate.md):
  the interactive REPL this item extends.
- [BE-0429](../../roadmaps/BE-0429-repl-tui-and-target-selectors/BE-0429-repl-tui-and-target-selectors.md):
  the terminal interface the attached session shares.
- [BE-0447](../../roadmaps/BE-0447-install-app-step/BE-0447-install-app-step.md):
  the install-app step and its device groups, which added the `/app/target` route, the closest
  existing runner retargeting mechanism.
- `bajutsu/repl/cli.py`, `BajutsuKit/Runner/Sources/RunnerUITest.swift`,
  `bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`.
