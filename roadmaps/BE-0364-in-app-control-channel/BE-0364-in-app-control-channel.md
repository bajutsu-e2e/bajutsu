**English** · [日本語](BE-0364-in-app-control-channel-ja.md)

# BE-0364 — Add a control channel so bajutsu can hide the touch visualization for a single capture

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0364](BE-0364-in-app-control-channel.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0364") |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

bajutsu configures the app under test once, at launch. `BajutsuKit` reads its launch environment in
`startIfEnabled()`, and after that bajutsu has no way to send the app anything: the only connection
from the app is outbound and fire-and-forget. Every in-app capability is therefore fixed for the life
of the process.

This item adds a channel in the other direction and puts one command on it: hide the in-app touch
visualization, or show it again. The command is delivered by having the app poll the collector it
already sends reports to, so the app opens no port. The channel carries only this one command,
because the visualization is evidence only and its visibility cannot change whether a step passes.

## Motivation

The `visualize-touches-in-app` item, not yet on `main`, proposes an in-app touch visualization. It
draws a marker at each touch the app receives, and the marks stay on screen until the next gesture,
so a step's screenshot includes them.

A `visual` assertion compares a step's screenshot against a checked-in baseline image. With the
visualization on, the markers appear in that screenshot and the baseline does not contain them, so
the comparison fails for a reason unrelated to the app. The correct behaviour is to hide the markers
for that one capture and show them again afterwards. Because the launch environment is the only
input, the visualization item disables itself for the whole scenario instead. That is coarser than
the problem, and it removes the touch evidence from the scenario being investigated.

Excluding the marker regions from the comparison is not a substitute. The marker moves with the
gesture, so the excluded region would have to be computed for each step, and excluding it would drop
the area where the touch landed from the comparison. That area is usually the one the assertion
checks.

Request stubbing has the same limit. `BajutsuNet.startIfEnabled()` loads a scenario's mock rules once
from the launch environment (`BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift:29`), so a scenario that
needs one response early and a different one later has to be split into two scenarios. This item does
not address that case; *Scope* below says why.

## Detailed design

The app and bajutsu already have an authenticated HTTP connection, and only one direction of it is
used. `NetworkCollector` (`bajutsu/evidence/network.py:116`) runs a `BaseHTTPRequestHandler` on
loopback, generates a token for each run (`:207`), and rejects a POST that does not carry it,
comparing the token in constant time (`check_token`, `:167`). The app receives the collector URL and
the token through `BAJUTSU_COLLECTOR` and `BAJUTSU_COLLECTOR_TOKEN`, and POSTs each exchange to the
collector's root path and each transition to `/transitions` (`:305`). The handler already defines
`do_GET`, which returns `200` and nothing else (`:315-317`), and no code calls it.

The channel therefore needs no new server, no new port, and no new authentication scheme. It needs a
command queue served by that `GET`.

### How a command is delivered

bajutsu adds a command to the queue. The app polls the collector, receives the command, applies it,
and POSTs a completion report on the connection it already uses. The app opens no socket, and the
token that protects the collector also protects the commands.

The completion report is required. If bajutsu sent a command and then waited a fixed time, that would
be the fixed sleep prime directive 2 forbids. Instead the run loop waits for the completion report as
a condition and fails with a message if it does not arrive. Either the command took effect before the
next step, or the step fails and says so. The screenshot is taken only after the app has reported
that the markers are hidden.

### Why the channel carries one command

The channel could carry more than one kind of command. It carries one, which keeps the rest of the
design simple.

**The channel carries no pass/fail judgement.** The only state a command can change is whether an
evidence-only overlay is drawn. Nothing on the channel can affect whether a step passes, and no
assertion reads from it, so the app under test never contributes to its own verdict. This is prime
directive 1 in its stricter form: the `run` gate stays machine-checkable and independent of the app.
A second command would not necessarily break that property, but it would need its own argument.
Mid-scenario stub replacement is the most likely request, and this item leaves it out deliberately:
changing a mocked response changes what a `request` assertion sees, which is a reasonable thing to
want and belongs in its own item.

**The channel is off unless asked for, and a launch env alone does not enable it.** `BajutsuKit`'s
README currently offers two options: gate the package out of release builds, or rely on the
`BAJUTSU_COLLECTOR` guard. This item removes the second option, because that guard is what stops
being sufficient. An adopter who links `BajutsuKit` into a release build and relies on it would
otherwise ship a binary in which an environment variable enables remote control of the app's
behaviour. The poll is therefore compiled out unless a build setting selects it, so the launch env is
never the only guard, and the README has to be rewritten to say that.

**The channel does not change application state.** It controls bajutsu's own in-app
instrumentation, not the application. A command that seeded app data or drove app navigation would
move per-app knowledge into the tool and break prime directive 3, whatever the transport allowed.

### The cost of polling

Polling avoids a listener, and the cost is latency: a command takes effect no sooner than the next
poll. Here that latency is paid twice per screenshot-comparing step, once to hide and once to show,
and each is serialized behind the wait for the completion report. That is acceptable for a step that
already runs an image comparison. The poll also runs a timer inside the app under test, so it must be
off unless the channel is enabled, and the implementation has to show that an idle poll does not
change the app's own timing, which is what a test measures.

The design also introduces one dependency worth naming. The touch visualization needs no collector
today, and a recorded run with no network features is its normal case. Delivering commands through the collector means a
scenario that wants the mid-capture toggle also starts a collector it would not otherwise need. A
second, purpose-built endpoint would avoid that, at the cost of a second server to secure;
*Alternatives considered* says why this item does not take that option.

### Work breakdown

The units below are mutually exclusive and collectively exhaustive.

| Unit | Work |
|---|---|
| 1 | The queue: a pending-command list on `NetworkCollector`, an authenticated `GET` that drains it, and a completion endpoint matched *before* `do_POST`'s catch-all, which stores every non-`/transitions` path as a network exchange — so a completion report never enters the exchanges a `request` assertion reads. The token check applies exactly as `do_POST` applies it today, and `clear()` drops the pending queue along with the exchanges, so a command left undrained by one scenario is not delivered to the next |
| 2 | The app side: a poll loop and command dispatch in `BajutsuKit`, compiled out unless an explicit build setting selects it and, when compiled in, activated by its own launch-env key and inert without it, so the launch env is never the only guard. Includes the completion POST on the existing report session |
| 3 | The wait and the one command: a condition wait on the completion report in the run loop, failing with a message on timeout, and the hide/show toggle applied around a screenshot-comparing step, replacing the `visualize-touches-in-app` item's whole-scenario opt-out. The wait reaches the app through the `Collector` protocol the pipeline already drives, so this unit also states which collectors carry a channel, and makes a command issued against one that does not fail with a message rather than be skipped. This unit has an ordering dependency: it cannot land before the `visualize-touches-in-app` item, because until then there is no visualization to toggle |
| 4 | Documentation in both languages: the channel, its build setting and launch-env key, the one-command scope and the reason for it, and the release-build gating this item makes mandatory |

## Alternatives considered

**Open a listener inside the app.** A small HTTP server in the app under test would receive commands
directly, with no polling latency. We set it aside for a security reason rather than an engineering
one: a listening socket inside the application under test is a larger exposure than a loopback client
that polls, it needs its own port allocation and per-device forwarding, and if its authentication is
wrong the app accepts commands from anything on the device. Polling's latency is small and bounded.

**A second control server, independent of the collector.** This removes the coupling described above,
so a run that wants the toggle would not have to start a network collector. It also means a second
authenticated server to get right: the token, the loopback binding, the port-collision handling, and
the constant-time comparison would each be written twice. Reusing the collector's server is the
smaller change, and if the coupling turns out to matter, splitting the endpoint later is a contained
refactor, because the app-side command dispatch does not depend on which server answered.

**Report the marker geometry and exclude those regions from the visual comparison.** The app could
POST each marker's rectangle on the connection it already has, and the comparison could exclude those
regions, with no inbound channel at all. We rejected this for the reason *Motivation* gives: it removes
the area where the gesture landed from the comparison, and it does so without reporting it. It also
lets an evidence-only feature decide what a machine-checkable assertion is allowed to see.

**Leave the granularity at one scenario.** This is the current behaviour, and it is useful: the app is
terminated and relaunched with each scenario's own launch environment, on the warm-runner path as well
as the cold one (`_resume_warm`,
[BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md)),
so a capability can already differ per scenario at no cost. The reason to go further is that the case
in *Motivation* occurs within one scenario, where the state that scenario has built up is part of what
is being tested, and splitting it changes what is under test.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — the collector's command queue, authenticated drain, and completion endpoint
- [ ] Unit 2 — the app-side poll loop and command dispatch, compiled out and env-gated by default
- [ ] Unit 3 — the completion condition wait and the hide/show toggle around a comparing step
- [ ] Unit 4 — bilingual documentation, including the release-build gating this item makes mandatory

## References

- [`BajutsuKit/README.md`](../../BajutsuKit/README.md) — the in-app package this channel extends, and
  the Safety guidance whose second option this item removes.
- [`docs/evidence.md`](../../docs/evidence.md) — the evidence kinds a run captures, and where the
  touch visualization is documented once the `visualize-touches-in-app` item lands.
- [BE-0291 — Reuse the XCUITest runner across scenarios to amortize cold startup](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md)
  — the relaunch behaviour that gives per-scenario granularity at no cost, which bounds what this item
  still has to add.
