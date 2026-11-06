**English** · [日本語](BE-0027-mock-server-external-ja.md)

# BE-0027 — `mockServer` (external mock)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0027](BE-0027-mock-server-external.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Deferred** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0027") |
| Topic | Miscellaneous / on hold |
<!-- /BE-METADATA -->

## Introduction

Only the config schema exists. Declarative in-protocol `mocks` (implemented) have superseded it, so whether an external-server approach has any use is open.

## Motivation

A deterministic E2E run needs the network under control. The same request must get the same
response every time, or pass/fail stops being reproducible. The config already carries a
`mockServer` schema (`cmd` / `port` / `stubs`) for one way to do that. It starts an external mock
server process beside the run and points the app at it. The idea was that the app would hit
`localhost:<port>` instead of its real backend, and the server would replay canned responses.

Since then, a different mechanism has met the same need. Scenario-level `mocks` are declarative,
in-protocol stubs intercepted inside Bajutsu's own network path. They are implemented and validated
on-device. They give offline, deterministic responses without a second process to launch,
health-check, and tear down.

The open question for this item is no longer "how do we build the external server?" but
"do we need it at all?". That question is why the item is **deferred** rather than scheduled.

## Detailed design

To be specified if this proposal is taken up. The schema (`config.py` `MockServer`:
`cmd` / `port` / `stubs`) is the only part that exists today, and it has no runtime effect.

This item is **deferred**, so the design is intentionally not fleshed out. Were it taken up, the design would have to clear the bar that the in-protocol `mocks` already meet:

- **Determinism.** The external server would have to reach a known-ready state before the scenario
  starts, and its teardown would have to be verified afterward. It would use no fixed `sleep`. A
  readiness or health condition does the waiting, consistent with "condition waits only". A process
  that is sometimes not yet listening when the app first calls it would reintroduce exactly the
  flakiness the project forbids.
- **App-agnostic config.** The config would stay where it is, at `apps.<name>.mockServer`. Per-app
  mock wiring then lives in config, and the runner stays unchanged across apps.
- **LLM-free gate.** A mock server is a deterministic process, so it does not touch the prime
  directive that no LLM enters the run/CI gate.

It would be **taken up** only if a concrete need appears that in-protocol `mocks` cannot cover.
One example is a backend whose behavior is too stateful or protocol-heavy to express as declarative stubs, where reusing an existing mock-server implementation is genuinely simpler than extending the in-protocol stub language: a real gRPC or WebSocket server, or a stateful session the app drives across many requests. Absent that need, the schema stays as a documented,
unwired placeholder.

## Alternatives considered

**Implement the external server now.** Rejected for this round. It adds a process to manage
(launch, readiness, teardown) and a second source of network behavior. The implemented in-protocol
`mocks` already cover the need. Building the server speculatively would carry complexity with no
driving use case.

**Remove the `mockServer` schema entirely.** Considered, but kept. The schema documents a real
design alternative and costs nothing while unwired. Deleting it would discard the option and force
a future taker to rediscover its shape. The honest middle ground is to keep the item deferred, with
`architecture.md` flagging the schema as "schema only, not wired".

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] TBD — enumerate the work breakdown (MECE) here once scoped.

## References

[architecture.md](../../docs/architecture.md#implementation-status), `config.py` `MockServer`
