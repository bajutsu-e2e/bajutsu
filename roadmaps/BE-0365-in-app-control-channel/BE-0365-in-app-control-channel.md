**English** · [日本語](BE-0365-in-app-control-channel-ja.md)

# BE-0365 — Give bajutsu a channel into the running app so an in-app capability can be toggled mid-scenario

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0365](BE-0365-in-app-control-channel.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0365") |
| Implementing PR | [#1699](https://github.com/bajutsu-e2e/bajutsu/pull/1699) (unit 1), [#1788](https://github.com/bajutsu-e2e/bajutsu/pull/1788) (unit 2), [#1916](https://github.com/bajutsu-e2e/bajutsu/pull/1916) (unit 3), [#1922](https://github.com/bajutsu-e2e/bajutsu/pull/1922) (unit 3 follow-up), [#2088](https://github.com/bajutsu-e2e/bajutsu/pull/2088) (unit 4), [#2092](https://github.com/bajutsu-e2e/bajutsu/pull/2092) (unit 4 follow-up), [#2608](https://github.com/bajutsu-e2e/bajutsu/pull/2608) (unit 5) |
| Topic | Driver & backend architecture |
| Related | [BE-0364](../BE-0364-in-app-control-channel/BE-0364-in-app-control-channel.md) |
<!-- /BE-METADATA -->

## Introduction

Everything bajutsu can say to the app under test, it says once, at launch. `BajutsuKit` reads its launch environment in `startIfEnabled()` and never hears from bajutsu again. Its one channel back is outbound and fire-and-forget. Every in-app capability stays fixed for the life of the process. A capability that needs to change *within* a scenario has no way to do so.

This item adds the missing direction: a command channel that bajutsu can use to reach a running app. The channel rides a poll on the collector the app already talks to. The app under test opens no port for it. The channel carries operational commands and never a judgement. The deterministic verdict stays untouched by it.

## Motivation

One shipped feature and one in-flight design both stop at the missing direction. Both give up the same way.

**The touch visualization**. [BE-0371](../BE-0371-visualize-touches-in-app/BE-0371-visualize-touches-in-app.md) ships an in-app touch visualization behind the `bajutsu run --touch-markers` flag. The visualization draws a marker at each touch the app receives. The marks persist until the next gesture, and a step's screenshot carries them.

The markers collide with a `visual` assertion. That assertion compares a screenshot against a checked-in baseline. The markers land in the same image the comparison reads. The right behaviour would hide the marks for that one capture and restore them after.

The launch environment is the one way in, and `--touch-markers` takes a coarser route as a result. The flag leaves the markers off for the *whole scenario* in one case. That case is a scenario whose verdict compares a screenshot. That remedy is coarser than the problem. It also costs the investigator the touch evidence for the scenario under investigation.

**Request stubbing**. Request stubbing hits the same limit. `BajutsuNet.startIfEnabled()` loads the scenario's rules once, from the launch environment. The loader lives at `BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift:29` in the package. A scenario might need one response at the start and a different one later. The scenario cannot express that need. The rules take their final form before the first step runs. The workaround splits the scenario in two, and the app relaunches between the halves. That workaround turns a single user journey into two and loses the state the journey had built.

Neither limit is a gap in those features. Both come from the same missing building block. The reason nobody has built it deserves a plain statement. An inbound channel lets something outside the app change the app's behaviour while it runs. We should introduce that capability on purpose and put a strong gate on it. That capability is no convenience to bolt on.

## Detailed design

The design starts from a fact that makes the cheap version possible. The app and bajutsu **already share an authenticated HTTP relationship**. The two sides use one direction of it so far. `NetworkCollector` in `bajutsu/evidence/network.py:116` works as follows:

- It runs a `BaseHTTPRequestHandler` on loopback.
- It mints a per-run token (`:207`) and refuses any `POST` that lacks the token. The comparison runs in constant time (`check_token`, `:167`).
- At launch, the app reads the collector's address from `BAJUTSU_COLLECTOR` in its environment. The app reads the token from `BAJUTSU_COLLECTOR_TOKEN` the same way. It sends each exchange to the root path with `POST`. It sends each transition to `/transitions` (`:305`).
- The handler's `do_GET` exists and does nothing but answer `200` (`:315-317`).

The channel needs no new server, no new port, and no new way to authenticate. It needs a queue behind that idle `GET`.

### The shape

**bajutsu enqueues, the app polls, the app acknowledges**. A command is a small JSON object. The object names a capability and the state the capability should take. The app polls the collector for pending commands and applies each one. The app then reports completion on the outbound channel it already uses. Nothing else changes. The app opens no socket, and the token that already guards the collector guards the commands too.

The acknowledgement is not decoration. It keeps the channel on the right side of prime directive 2 here. Consider a caller that issued a command and then slept for "long enough". That sleep would be the fixed sleep the directive forbids. Instead, the run loop **condition-waits on the acknowledgement**. When the acknowledgement does not arrive, the step stops with a clear error. A command either took effect before the next step with proof, or the step does not pass. The same wait makes the touch-marker case correct rather than hopeful. The runner takes the screenshot after the app has confirmed it hid the marks.

### What the channel must not become

Three boundaries matter more than the transport.

**It carries no judgement**. A command names a capability and a state. Nothing on this channel may influence whether a step passes, and no assertion may read from it. Otherwise the app under test would take part in its own verdict. Prime directive 1 forbids that outcome in its sharper form. The `run` gate stays machine-checkable and app-independent.

**It stays off unless asked for, behind a stronger gate than the launch env alone**. Consider an adopter who links `BajutsuKit` into a release build and relies on the launch-env guard. Without a stronger gate, that adopter would ship a risky binary. In that binary, an environment variable turns on remote control of the app's own behaviour. Today, the `BajutsuKit` README offers a choice. Adopters either gate the package out of release builds *or* rely on the `BAJUTSU_COLLECTOR` guard.

This item retires the second option, because the collector guard stops being enough on its own. The gating becomes load-bearing rather than one of two alternatives. The README must say so in those terms.

**It never becomes a back door into the app's own state**. The channel exists to control bajutsu's own in-app instrumentation. Examples are the visualization and the stub table. It does not reach into the application's state. A command that seeded app data or drove app navigation would move per-app knowledge into the tool. That move would break prime directive 3, whatever the transport allowed.

### The polling cost, stated

Polling buys the absence of a listener at the price of latency. A command takes effect no sooner than the next poll. For the touch-marker case, each screenshot-comparing step pays that latency twice. One payment hides the markers, and the other restores them. The cost sits serialized behind the acknowledgement wait. That cost suits an investigation flag. It would not suit a feature that runs on every step.

The poll also runs a timer inside the app under test. The timer must stay off unless someone enables the channel. The implementation has to show that an idle poll does not disturb the app's own timing. That timing is what a test measures in the first place.

One coupling deserves a name before it surprises someone. The touch visualization needs no collector today. A plain recorded run with no network features at all is its normal case. Routing commands through the collector changes that. A scenario that wants mid-scenario control also starts a collector it would not otherwise need. The alternative is a second, purpose-built endpoint. That endpoint buys independence at the cost of a second server to secure. The *Alternatives considered* section records why this item does not take it.

### Work breakdown

The units below are mutually exclusive and collectively exhaustive.

| Unit | Work |
|---|---|
| 1 | The queue. `NetworkCollector` gains a pending-command list and an authenticated `GET` that drains the list. It also gains an acknowledgement endpoint, matched *ahead of* the catch-all route in `do_POST` today. That catch-all stores every other path as a network exchange. Matching first keeps each completion report out of the exchanges that a `request` assertion reads. The report says whether the app *applied* the command. When the app did not apply it, the report carries the app's own reason. Unit 3's wait then never receives "applied it" and "drained it and could not apply it" as the same message. The drain checks the token the same way `do_POST` checks it today. `clear()` drops the pending queue alongside the exchanges. A command that one scenario left undrained never reaches the next scenario |
| 2 | The app side. `BajutsuKit` gains a poll loop and command dispatch. The code compiles out unless an explicit build setting selects it. When compiled in, it activates on its own launch-env key and stays inert without that key. The launch env never guards the channel by itself. The unit includes the acknowledgement `POST` on the existing report connection |
| 3 | The wait. The run loop condition-waits on the acknowledgement and stops with a clear error on timeout. The unit also ships the first command. That command toggles the BE-0371 touch visualization around a screenshot-comparing step. The toggle replaces the whole-scenario opt-out that `--touch-markers` performs today. The wait reaches the app through the `Collector` protocol that the pipeline already drives. This unit also states which collectors carry a channel. A command sent to a collector without a channel stops with a clear error rather than a silent skip |
| 4 | The second command. A mid-scenario stub-table replacement lets a scenario change a mocked response without splitting the scenario in two |
| 5 | Documentation in both languages. It covers the channel, its activation key, and the boundaries above. It also covers the release-build gating that the channel makes mandatory |

## Alternatives considered

**Open a listener inside the app**. A small HTTP server in the app under test would take commands directly. That server would avoid the polling latency. We set the option aside on the security boundary rather than on the engineering. A listening socket inside the app under test exposes a larger surface than a loopback poll does. The socket needs its own port allocation and per-device forwarding. A mistake in its authentication yields an app that accepts commands from anything on the device. Polling costs a small, bounded latency. The surface a listener adds is neither small nor bounded.

**A second, purpose-built control server, independent of the collector**. This option removes the coupling named above. A run that wants mid-scenario control would not have to start a network collector. The option also doubles the number of authenticated servers to get right. We would write the token, the loopback binding, and the port-collision handling a second time. The constant-time comparison would need a second copy too. Reusing the collector's server is the smaller change. If the coupling proves to bite in practice, splitting the endpoint later stays a contained refactor. The app-side command dispatch does not care which server answered.

**Report the marker geometry and mask those regions in the visual comparison**. The app could send each marker's rectangle with `POST` on the outbound channel it already has. The comparison could then exclude those regions, with no inbound channel at all. We rejected the approach because it hides the region where the gesture landed from the comparison. That region tends to be the one the assertion exists to check, and nothing would flag the omission. The approach also puts a feature meant for evidence on the verdict path. That feature would decide what a machine-checkable assertion gets to see.

**Leave it at per-scenario granularity**. This option is the status quo, and it has real value. The runner terminates the app and relaunches it with each scenario's own launch environment. The warm-runner path does so as much as the cold one; see `_resume_warm` and [BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md) for the details. A capability can already differ per scenario at no cost. The reason to go further is that both motivating cases sit *within* one scenario. There, the journey's own state is the subject of the test. Splitting the scenario changes what the test covers.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — the collector's command queue, authenticated drain, and acknowledgement endpoint
- [x] Unit 2 — the app-side poll loop and command dispatch, env-gated and inert by default
- [x] Unit 3 — the acknowledgement condition wait, and the touch-visualization toggle as the first command
- [x] Unit 4 — mid-scenario stub-table replacement as the second command
- [x] Unit 5 — bilingual documentation, including the release-build gating this makes mandatory

Log:

- [#1699](https://github.com/bajutsu-e2e/bajutsu/pull/1699) — unit 1: the collector's pending-command queue and the
  authenticated `GET /commands` drain. It also adds the `/commands/ack` report endpoint. The collector matches
  that endpoint ahead of the catch-all route in `do_POST` today. A report thus never enters the exchanges a
  `request` assertion reads. The `applied` field of the report has no default value. That keeps a command
  the app drained but could not apply distinct from one it applied. `clear()` scopes the channel per
  scenario while the id counter survives. An unknown `GET` path now answers 404 rather than an empty 200
  response. The same change rejected BE-0364 as a duplicate of this item. It folded the two sharper points
  of BE-0364 into unit 1 and unit 2 of this item. It also retired this item's stale claim that the BE-0371
  touch visualization had not yet reached `main` at the time.

- [#1788](https://github.com/bajutsu-e2e/bajutsu/pull/1788) — unit 2: the app-side poll loop and command
  dispatch in `BajutsuKit` itself. A compilation condition, `BAJUTSU_ENABLE_CONTROL_CHANNEL`, must select the
  channel first. After that, the launch-env key `BAJUTSU_CONTROL_CHANNEL` can reach the channel. The launch
  environment never guards the channel alone. The app reads a command's capability as the string bajutsu
  sent, not as an enum case. An app older than the bajutsu driving it then refuses an unsupported
  capability by name. The app does not drop the command and leave unit 3's wait to time out with no reason
  given. Unit 4's stub table already survives that decoding. The table is a sibling shape that carries no
  `enabled` field. An empty drain schedules no main-thread work. A drain answered with `401` or `404` ends the loop.
  The loop then leaves no timer running in the app for the rest of the process's life. The Swift gate now
  builds and tests both configurations. It runs the unflagged one first, since adopters link that
  configuration.

- [#1916](https://github.com/bajutsu-e2e/bajutsu/pull/1916) — unit 3: the condition wait
  on acknowledgement in `control_channel.py`. The module lives under `orchestrator/` in the package. The wait sits on the existing
  `deadline_ticks` skeleton. The touch-visualization toggle is the first command. A `visual` capture
  happens in two places: the initial `expect` capture, and the post-alert-dismiss retry. `run_scenario`
  hides the markers around both captures. A command that cannot prove it took effect makes the
  scenario not pass, as an ordinary failing step does. `--touch-markers` no longer skips a screenshot-comparing scenario outright. The
  flag arms the channel for each scenario that can carry one. Such a scenario uses the `xcuitest` actuator
  with network collection on. The flag also requires both actuator selectors to answer with `xcuitest` as
  their choice. BE-0240 chooses the actuator per scenario. The channel rides collectors that the run
  pre-starts from the run-level actuator. A multi-candidate `--backend` thus has to answer `xcuitest`
  twice over. Everywhere else, the flag falls back to the skip from before BE-0365. That fallback covers a
  scenario that resolved to another backend and a run with network off. It also covers a scenario that
  declined the channel by pinning `BAJUTSU_CONTROL_CHANNEL` to any value but `"1"` in its environment.
  One exception applies: a scenario that pinned `BAJUTSU_TOUCH_MARKERS: "1"` itself keeps drawing the
  markers. `setdefault` cannot turn those markers off. Such a scenario may also pin
  `BAJUTSU_CONTROL_CHANNEL: "1"` on a run that cannot carry the channel. That scenario then fails outright.
  A scenario with no `visual` verdict that pinned the channel key alone gets no marker key at all. Writing a
  marker key would complete the pair the run loop reads. It would arm a channel the scenario never asked
  for. The run names on stderr which of the six outcomes applied.

- [#1922](https://github.com/bajutsu-e2e/bajutsu/pull/1922) — unit 3 follow-up for the launch env.
  `_hides_touch_markers` and `_apply_touch_markers` both read the environment the app launches with. A target's own
  `launchEnv` merges *underneath* the scenario's own at launch time. Consider a target-level
  `BAJUTSU_TOUCH_MARKERS`/`BAJUTSU_CONTROL_CHANNEL` pin, as opposed to one that `--touch-markers` writes
  per scenario. Before this fix, such a pin read as a scenario that pinned nothing. The app then drew
  markers that the run loop never hid for a `visual` capture. A new `target_launch_env` parameter carries
  the target's `launchEnv` into both functions. The functions merge it the same way the launch itself does.

- [#2088](https://github.com/bajutsu-e2e/bajutsu/pull/2088) — unit 4: a `setMocks` step replaces the
  running app's whole stub table mid-scenario. The table travels as `AppStubTableCommand` over the
  channel. That model is a sibling of the toggle `AppCommand`, told apart by the `capability` field. Its
  wire shape matches that of `BAJUTSU_MOCKS` in full. The run loop waits on the app's acknowledgement as
  unit 3's toggle does. A refusal or a timeout fails the `setMocks` step itself. `run` writes
  `BAJUTSU_CONTROL_CHANNEL=1` for every scenario that swaps its mocks. Where the channel cannot reach the
  app, `run` stops before any device work. Web, Android, `fake`, and `--no-network` all stop there. A
  declined channel key and a swap placed off the primary app stop there too. A target's app-wide phases
  refuse `setMocks` at config load. BajutsuKit now locks its stub table. It refuses a delivered table whole
  when any rule would not match as written. A catch-all or never-matching stub thus never reads as applied.

- [#2092](https://github.com/bajutsu-e2e/bajutsu/pull/2092) — unit 4 follow-up: BajutsuKit's
  `replace(with:)` releases the stub-table lock with `defer`, as the `rules` getter already did. A return
  added later inside the critical section thus cannot leave the table locked. A comment in
  `_mock_swap_problem` records why `targets[0]` is the primary. BE-0436 refuses a `primaryTarget` that
  names any other entry.

- [#2608](https://github.com/bajutsu-e2e/bajutsu/pull/2608) — unit 5: the channel's own documentation. `docs/network.md` gains a section that
  describes the channel in one place. The section covers how a command travels and how the app
  acknowledges it. It also covers the two commands the channel carries and the backends that can carry
  it. It names the two in-app gates, the three boundaries, and what polling costs. `BajutsuKit/README.md`
  lists `BAJUTSU_CONTROL_CHANNEL` among its launch-env keys. The README describes the channel and its
  compilation condition. It makes the release-build gate mandatory rather than one of two options. Both
  `docs/` pages carry a mirror under `docs/ja/` as well.

## References

- [`BajutsuKit/README.md`](../../BajutsuKit/README.md) — the in-app package this channel extends. Its
  Safety guidance becomes mandatory under this channel rather than advisory.
- [`docs/evidence.md`](../../docs/evidence.md) — the evidence kinds a run captures. Its `--touch-markers`
  section closes with a paragraph that states the limit this item removes.
- [BE-0371](../BE-0371-visualize-touches-in-app/BE-0371-visualize-touches-in-app.md) — the shipped
  visualization that draws a marker at each touch the app receives. Unit 3 toggles that visualization,
  which settles its ordering dependency.
- [BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md)
  — reuses the XCUITest runner across scenarios to amortize cold startup. Its relaunch behaviour gives
  per-scenario granularity for free. That behaviour bounds what this item still has to add.
- [BE-0364](../BE-0364-in-app-control-channel/BE-0364-in-app-control-channel.md) — a parallel proposal
  for the same channel. It aimed to hide the touch visualization for a single capture. This item superseded
  BE-0364, which now stands rejected. The work breakdown above absorbs two details that BE-0364 argued
  more precisely.
