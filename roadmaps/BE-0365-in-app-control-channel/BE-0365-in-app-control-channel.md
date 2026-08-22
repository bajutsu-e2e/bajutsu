**English** · [日本語](BE-0365-in-app-control-channel-ja.md)

# BE-0365 — Give bajutsu a channel into the running app so an in-app capability can be toggled mid-scenario

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0365](BE-0365-in-app-control-channel.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0365") |
| Implementing PR | [#1699](https://github.com/bajutsu-e2e/bajutsu/pull/1699) (unit 1) |
| Topic | Driver & backend architecture |
| Related | [BE-0364](../BE-0364-in-app-control-channel/BE-0364-in-app-control-channel.md) |
<!-- /BE-METADATA -->

## Introduction

Everything bajutsu can say to the app under test, it says once, at launch. `BajutsuKit` reads its
launch environment in `startIfEnabled()` and never hears from bajutsu again: its only channel back is
outbound and fire-and-forget. Every in-app capability is therefore fixed for the life of the process,
and a capability that needs to change *within* a scenario cannot. This item adds the missing
direction — a command channel bajutsu can use to reach a running app — built as a poll on the
collector the app already talks to, so no port is opened inside the app under test. The channel
carries operational commands only, never a judgement, so the deterministic verdict is untouched.

## Motivation

One shipped feature and one in-flight design are bounded by the missing direction, and both give up
the same way.

[BE-0371](../BE-0371-visualize-touches-in-app/BE-0371-visualize-touches-in-app.md) ships an in-app
touch visualization behind `bajutsu run --touch-markers`: it draws a marker at each touch the app
receives, and the marks persist until the next gesture, so a step's screenshot carries them. That
collides with a `visual` assertion, which compares a screenshot against a checked-in baseline: the
markers land in the very image the comparison reads. The right behaviour would be to hide the marks
for that one capture and restore them after. Because the launch environment is the only way in,
`--touch-markers` instead leaves the markers off for the *whole scenario* whenever that scenario's
verdict compares a screenshot — coarser than the problem, and it costs the investigator the touch
evidence for exactly the scenario they were looking at.

Request stubbing is bounded the same way. `BajutsuNet.startIfEnabled()` loads the scenario's rules
once, from the launch environment (`BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift:29`), so a
scenario that needs one response early and a different one later cannot express it: the rules are
baked before the first step runs. The workaround is to split the scenario in two so the app is
relaunched between them, which turns a single user journey into two and loses the state the journey
had built.

Neither limit is a gap in those features. Both are the same missing primitive, and the reason it has
never been built is worth stating plainly rather than leaving implied: an inbound channel means
something outside the app can change the app's behaviour while it runs, which is a capability to
introduce deliberately and gate hard, not a convenience to bolt on.

## Detailed design

The design starts from a fact that makes the cheap version possible: the app and bajutsu **already
have an authenticated HTTP relationship**, and only one direction of it is used. `NetworkCollector`
(`bajutsu/evidence/network.py:116`) runs a `BaseHTTPRequestHandler` on loopback, mints a per-run
token (`:207`), and rejects any POST that does not bear it, comparing in constant time
(`check_token`, `:167`). The app receives the collector's URL and that token through
`BAJUTSU_COLLECTOR` / `BAJUTSU_COLLECTOR_TOKEN` and POSTs each exchange to the collector's root path and each
transition to `/transitions` (`:305`). The handler's `do_GET` exists and does nothing but answer `200` (`:315-317`).

So the channel needs no new server, no new port, and no new authentication scheme. It needs a queue
behind that idle `GET`.

### The shape

**bajutsu enqueues, the app polls, the app acknowledges.** A command is a small JSON object naming a
capability and the state it should take. The app polls the collector for pending commands, applies
what it gets, and reports completion on the outbound channel it already uses. Nothing else changes:
the app opens no socket, and the token that already guards the collector guards the commands.

The acknowledgement is not decoration — it is what keeps the channel off the wrong side of prime
directive 2. A caller that issued a command and then slept for "long enough" would be exactly the
fixed sleep the directive forbids. Instead the run loop **condition-waits on the acknowledgement**
and fails loudly if it does not arrive, so a command either provably took effect before the next step
or the step fails saying so. That is also what makes the touch-marker case correct rather than
hopeful: the screenshot is taken only after the app has confirmed the marks are hidden.

### What the channel must not become

Three boundaries matter more than the transport.

**It carries no judgement.** A command names a capability and a state. Nothing on this channel may
influence whether a step passes, and no assertion may read from it — otherwise the app under test
would be participating in its own verdict, which prime directive 1 forbids in the sharper form: the
`run` gate stays machine-checkable and app-independent.

**It is off unless asked for, and gated harder than a launch env alone.** An adopter who links
`BajutsuKit` into a release build and relies on the launch-env guard would otherwise ship a binary in
which an environment variable turns on remote control of the app's own behaviour. `BajutsuKit`'s README today offers a
choice: gate the package out of release builds *or* rely on the `BAJUTSU_COLLECTOR` guard. This item
retires the second option, since that guard is exactly what stops being sufficient — the gating
becomes load-bearing rather than one of two alternatives, and the README must say so in those terms.

**It never becomes a way to make the app easier to test.** The channel exists to control bajutsu's
own in-app instrumentation — the visualization, the stub table — and not to reach into the
application's state. A command that seeded app data or drove app navigation would move per-app
knowledge into the tool and break prime directive 3, whatever the transport allowed.

### The polling cost, stated

Polling buys the absence of a listener at the price of latency: a command takes effect no sooner than
the next poll. For the touch-marker case that latency is paid once per screenshot-comparing step,
serialized behind the acknowledgement wait, which is acceptable for an investigation flag and would
not be for something on every step. The poll also runs a timer inside the app under test, so it must
be off unless the channel is enabled, and the implementation has to show that an idle poll does not
perturb the app's own timing — the very thing a test is measuring.

One coupling is worth naming before it surprises someone. The touch visualization needs no collector
today: a plain recorded run with no network features at all is its normal case. Routing commands
through the collector means a scenario that wants mid-scenario control also starts a collector it
would not otherwise need. The alternative — a second, purpose-built endpoint — buys independence at
the cost of a second server to secure, and the *Alternatives considered* section records why this
item does not take it.

### Work breakdown

The units below are mutually exclusive and collectively exhaustive.

| Unit | Work |
|---|---|
| 1 | The queue: a pending-command list on `NetworkCollector`, an authenticated `GET` that drains it, and an acknowledgement endpoint matched *ahead of* `do_POST`'s catch-all, which stores every other path as a network exchange — so a completion report never enters the exchanges a `request` assertion reads. The report says whether the app *applied* the command, and carries the app's own reason when it did not, so "applied it" and "drained it and could not apply it" never reach unit 3's wait as the same message. The token check applies exactly as `do_POST` applies it today, and `clear()` drops the pending queue alongside the exchanges, so a command one scenario left undrained is never delivered to the next |
| 2 | The app side: a poll loop and command dispatch in `BajutsuKit`, compiled out unless an explicit build setting selects it and, when compiled in, activated by its own launch-env key and inert without it — so the launch env is never the only guard. Includes the acknowledgement POST on the existing report session |
| 3 | The wait: a condition wait on the acknowledgement in the run loop, failing loudly on timeout, and the first command — toggling BE-0371's touch visualization around a screenshot-comparing step, replacing the whole-scenario opt-out `--touch-markers` performs today. The wait reaches the app through the `Collector` protocol the pipeline already drives, so this unit also states which collectors carry a channel, and makes a command issued against one that does not fail loudly rather than be skipped |
| 4 | The second command: a mid-scenario stub-table replacement, so a scenario can change a mocked response without being split in two |
| 5 | Documentation in both languages: the channel, its activation key, the boundaries above, and the release-build gating the channel makes mandatory |

## Alternatives considered

**Open a listener inside the app.** A small HTTP server in the app under test would take commands
directly, with none of the polling latency. We set it aside on the security boundary rather than on
the engineering: a listening socket inside the application under test is a materially larger surface
than a poll on a loopback client, it needs its own port allocation and per-device forwarding, and the
failure mode of getting its authentication wrong is an app that accepts commands from anything on the
device. The latency polling costs is small and bounded; the surface a listener adds is neither.

**A second, purpose-built control server, independent of the collector.** This removes the coupling
named above — a run wanting mid-scenario control would not have to start a network collector. It also
doubles the number of authenticated servers to get right, and the token, the loopback binding, the
port-collision handling, and the constant-time comparison would all be written a second time. Reusing
the collector's server is the smaller change; if the coupling proves to bite in practice, splitting
the endpoint later is a contained refactor, because the app-side command dispatch does not care which
server answered.

**Report the marker geometry and mask those regions in the visual comparison.** The app could POST
each marker's rectangle on the outbound channel it already has, and the comparison could exclude
those regions — no inbound channel at all. We rejected the approach because it blinds the comparison
exactly where the gesture landed, which is usually the region the assertion exists to check, and it
does so silently. It also puts an evidence-only feature on the verdict path, deciding what a
machine-checkable assertion is allowed to see.

**Leave it at per-scenario granularity.** This is the status quo, and it is not nothing: because the
app is terminated and relaunched with each scenario's own launch environment — on the warm-runner
path as much as the cold one (`_resume_warm`,
[BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md)) —
a capability can already differ per scenario at no cost. The reason to go further is that the two
motivating cases are both *within* one scenario, where the journey's own state is the thing being
tested and splitting it changes what is under test.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — the collector's command queue, authenticated drain, and acknowledgement endpoint
- [ ] Unit 2 — the app-side poll loop and command dispatch, env-gated and inert by default
- [ ] Unit 3 — the acknowledgement condition wait, and the touch-visualization toggle as the first command
- [ ] Unit 4 — mid-scenario stub-table replacement as the second command
- [ ] Unit 5 — bilingual documentation, including the release-build gating this makes mandatory

Log:

- [#1699](https://github.com/bajutsu-e2e/bajutsu/pull/1699) — unit 1: the collector's pending-command queue, the authenticated `GET /commands`
  that drains it, and the `/commands/ack` report endpoint, matched ahead of `do_POST`'s catch-all so a
  report never enters the exchanges a `request` assertion reads. The report's `applied` carries no
  default, so a command the app drained but could not apply is a distinct message from one it applied.
  `clear()` scopes the channel per scenario while the id counter survives, and an unknown `GET` path
  now answers 404 rather than an empty 200. Rejected BE-0364 as a duplicate of this item in the same
  change, folding its two sharper points into units 1 and 2, and retired this item's stale claim that
  BE-0371's touch visualization was not yet on `main`.

## References

- [`BajutsuKit/README.md`](../../BajutsuKit/README.md) — the in-app package this channel extends, and
  the Safety guidance the channel makes mandatory rather than advisory.
- [`docs/evidence.md`](../../docs/evidence.md) — the evidence kinds a run captures, including the
  `--touch-markers` section whose closing paragraph states the limit this item removes.
- [BE-0371 — Draw a marker at each touch the app receives so recordings show where a gesture landed](../BE-0371-visualize-touches-in-app/BE-0371-visualize-touches-in-app.md)
  — the shipped visualization unit 3 toggles; its ordering dependency is therefore already settled.
- [BE-0291 — Reuse the XCUITest runner across scenarios to amortize cold startup](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md)
  — the relaunch behaviour that gives per-scenario granularity for free, and therefore bounds what
  this item still has to add.
- [BE-0364 — Add a control channel so bajutsu can hide the touch visualization for a single capture](../BE-0364-in-app-control-channel/BE-0364-in-app-control-channel.md)
  — the same channel proposed in parallel and rejected in favour of this item; two details it argued
  more precisely are folded into the work breakdown above.
