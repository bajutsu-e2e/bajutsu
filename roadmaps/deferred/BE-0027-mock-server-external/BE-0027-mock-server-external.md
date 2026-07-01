**English** · [日本語](BE-0027-mock-server-external-ja.md)

# BE-0027 — `mockServer` (external mock)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0027](BE-0027-mock-server-external.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal (deferred)** |
| Topic | Miscellaneous / on hold |
<!-- /BE-METADATA -->

## Introduction

Only the config schema exists. It has been superseded by declarative in-protocol `mocks` (implemented), so whether an external-server approach is really needed is open.

## Motivation

A deterministic E2E run needs the network under control: the same request must get the same
response every time, or pass/fail stops being reproducible. The config already carries a
`mockServer` schema (`cmd` / `port` / `stubs`) for one way to do that — start an external mock
server process beside the run and point the app at it. The idea was that an app could be made to
hit `localhost:<port>` instead of its real backend, with the server replaying canned responses.

Since then, the same need has been met a different way. Scenario-level `mocks` — declarative,
in-protocol stubs intercepted inside Bajutsu's own network path — are implemented and validated
on-device. They give offline, deterministic responses without a second process to launch, health-
check, and tear down. That makes the open question for this item not "how do we build the external
server?" but "is it needed at all?", which is why it is **deferred** rather than scheduled.

## Detailed design

To be specified if this proposal is taken up. The schema (`config.py` `MockServer`:
`cmd` / `port` / `stubs`) is the only part that exists today; it has no runtime effect.

This item is **deferred**, so the design is intentionally not fleshed out. Were it taken up, it
would have to clear the bar the in-protocol `mocks` already meet:

- **Determinism.** The external server would have to be brought up to a known-ready state before
  the scenario starts and verified torn down after, with no fixed `sleep` — a readiness/health
  condition, consistent with "condition waits only". A process that is sometimes not yet listening
  when the app first calls it would reintroduce exactly the flakiness the project forbids.
- **App-agnostic config.** It would stay where it is — `apps.<name>.mockServer` — so per-app mock
  wiring lives in config and the runner stays unchanged across apps.
- **LLM-free gate.** A mock server is a deterministic process, so it does not touch the prime
  directive that no LLM enters the run/CI gate.

It would be **taken up** only if a concrete need appears that in-protocol `mocks` cannot cover —
for example a backend whose behavior is too stateful or protocol-heavy to express as declarative
stubs (a real gRPC or WebSocket server, a stateful session the app drives across many requests),
where reusing an existing mock-server implementation is genuinely simpler than extending the
in-protocol stub language. Absent that need, the schema stays as a documented, unwired
placeholder.

## Alternatives considered

**Implement the external server now.** Rejected for this round: it adds a process to manage
(launch, readiness, teardown) and a second source of network behavior, for a need that the
implemented in-protocol `mocks` already cover. Building it speculatively would be carrying
complexity with no driving use case.

**Remove the `mockServer` schema entirely.** Considered, but kept. The schema documents a real
design alternative and costs nothing while unwired; deleting it would discard the option and force
a future taker to rediscover the shape. Keeping it deferred — with `architecture.md` flagging it
as "schema only, not wired" — is the honest middle ground.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] TBD — enumerate the work breakdown (MECE) here once scoped.

## References

[architecture.md](../../../docs/architecture.md#implementation-status), `config.py` `MockServer`
